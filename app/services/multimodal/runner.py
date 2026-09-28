"""多模态单次探测任务：请求、原始证据、能力判定与历史记录。"""
import asyncio
import copy
import json
import time
from datetime import datetime

import httpx

from ..key_store import resolve
from ..runner_bridge import build_chat_url
from ...settings import MM_INLINE_WARN_BYTES, MM_MAX_INLINE_REQUEST_BYTES
from . import media as media_service
from . import store
from .protocols import build_payload

MAX_RAW = 100000
MAX_TEXT = 8000

_tasks: dict[int, asyncio.Task] = {}
_progress: dict[int, dict] = {}


def progress_for(run_id: int) -> dict:
    return _progress.get(run_id, {})


def _set_progress(run_id: int, **kw) -> None:
    _progress.setdefault(run_id, {}).update(kw)


def _classify(status_code: int | None, error: str) -> str:
    if error or status_code is None:
        return "UNKNOWN"
    if 400 <= status_code < 500:
        return "REJECT"
    if 200 <= status_code < 300:
        return "ACCEPT"
    return "ERROR"


def _judge(status_code: int | None, output: str, keywords: list[str],
           error: str = "", request_size_bytes: int = 0) -> tuple[str, str]:
    if error or status_code is None:
        return "error", "请求异常，无法判断"
    if status_code in (401, 403, 429):
        return "inconclusive", f"服务端返回 HTTP {status_code}，鉴权或限流导致不可判断"
    if status_code >= 500:
        reason = f"服务端返回 HTTP {status_code}"
        if request_size_bytes > MM_INLINE_WARN_BYTES:
            reason += (
                f"；请求体约 {request_size_bytes / 1024 / 1024:.1f} MB，"
                "可能超过供应商请求体限制"
            )
        return "error", reason
    if status_code >= 400:
        return "rejected", f"模型拒绝该多模态请求：HTTP {status_code}"
    if not (200 <= status_code < 300):
        return "inconclusive", f"HTTP {status_code} 不是预期的成功响应"
    if not output.strip():
        return "partial", "请求成功，但响应没有可判定文本"
    if not keywords:
        return "accepted", "请求成功；未配置期望关键词，暂按接口接受"
    haystack = output.lower()
    missing = [k for k in keywords if k and k.lower() not in haystack]
    if missing:
        return "partial", f"接口接受，但回答未命中期望内容：{', '.join(missing)}"
    return "pass", "接口接受且回答命中全部期望内容"


def _finish_reason_hint(body: dict | None) -> str:
    if not isinstance(body, dict):
        return ""
    choices = body.get("choices") or [{}]
    return str((choices[0] or {}).get("finish_reason") or "")


def _safe_request(payload: dict) -> dict:
    """避免把完整 Base64 媒体重复写入数据库和接口响应。"""
    safe = copy.deepcopy(payload)
    messages = safe.get("messages") or []
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "image_url":
                url = (part.get("image_url") or {}).get("url") or ""
                if url.startswith("data:"):
                    part["image_url"]["url"] = f"<data-url {len(url)} chars>"
            elif part.get("type") == "video_url":
                url = (part.get("video_url") or {}).get("url") or ""
                if url.startswith("data:"):
                    part["video_url"]["url"] = f"<data-url {len(url)} chars>"
    return safe


async def _execute(*, run_id: int, conn: dict, model_row: dict, media: dict,
                   input_mode: str, prompt: str, expected: list[str], config: dict) -> None:
    store.update_run(run_id, status="running")
    _set_progress(run_id, status="running", done=0, total=1, current="准备请求")
    try:
        media_ref, actual_mode = await media_service.materialize(media, input_mode)
        payload = build_payload(
            model=model_row["model"],
            kind=media["kind"],
            media_ref=media_ref,
            prompt=prompt,
            stream=bool(config.get("stream", False)),
            max_tokens=int(config.get("max_tokens", 512)),
            temperature=config.get("temperature"),
            extra_params=model_row.get("_extra_params"),
        )
        request_size_bytes = len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        if (
            actual_mode == "inline"
            and request_size_bytes > MM_MAX_INLINE_REQUEST_BYTES
            and not config.get("allow_large_inline")
        ):
            raise media_service.MediaError(
                f"实际请求体约 {request_size_bytes / 1024 / 1024:.1f} MB，"
                f"超过 {MM_MAX_INLINE_REQUEST_BYTES / 1024 / 1024:.0f} MB 安全线；"
                "请改用公网 URL，或开启“强制发送”。"
            )
        url = build_chat_url(conn["base_url"], conn.get("endpoint") or None)
        headers = {"Content-Type": "application/json"}
        api_key = resolve(conn)
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        _set_progress(run_id, current="发送多模态请求")
        t0 = time.perf_counter()
        usage: dict = {}
        text = ""
        raw = ""
        raw_truncated = False
        status: int | None = None
        body = None
        ttft = None
        error = ""
        try:
            async with httpx.AsyncClient(timeout=float(config.get("timeout", 90))) as client:
                if payload.get("stream"):
                    parts: list[str] = []
                    raw_lines: list[str] = []
                    async with client.stream("POST", url, json=payload, headers=headers) as response:
                        status = response.status_code
                        async for line in response.aiter_lines():
                            if len(raw_lines) < 300:
                                raw_lines.append(line)
                            elif not raw_truncated:
                                raw_truncated = True
                            if not line.startswith("data:"):
                                continue
                            data = line[5:].strip()
                            if not data or data == "[DONE]":
                                continue
                            try:
                                chunk = json.loads(data)
                            except json.JSONDecodeError:
                                continue
                            if chunk.get("usage"):
                                usage = chunk["usage"]
                            choice = (chunk.get("choices") or [{}])[0] or {}
                            delta = choice.get("delta") or {}
                            content = delta.get("content") or ""
                            if content:
                                if ttft is None:
                                    ttft = time.perf_counter() - t0
                                parts.append(content)
                    text = "".join(parts)
                    raw = "\n".join(raw_lines)
                else:
                    response = await client.post(url, json=payload, headers=headers)
                    status = response.status_code
                    raw = response.text
                    try:
                        body = response.json()
                    except Exception:  # noqa: BLE001
                        body = None
                    if isinstance(body, dict):
                        usage = body.get("usage") or {}
                        choice = (body.get("choices") or [{}])[0] or {}
                        text = ((choice.get("message") or {}).get("content") or "")
                    else:
                        text = response.text
        except Exception as exc:  # noqa: BLE001
            error = f"{type(exc).__name__}: {exc}"
        raw_truncated = raw_truncated or len(raw) > MAX_RAW
        raw = raw[:MAX_RAW]
        output_truncated = len(text) > MAX_TEXT
        text = text[:MAX_TEXT]
        behavior = _classify(status, error)
        verdict, reason = _judge(
            status, text, expected, error, request_size_bytes=request_size_bytes,
        )
        item = {
            "request": payload,
            "raw_response": raw,
            "raw_response_truncated": raw_truncated,
            "output_text": text,
            "output_text_truncated": output_truncated,
            "request_size_bytes": request_size_bytes,
            "usage": usage or {},
            "status_code": status,
            "behavior_class": behavior,
            "prompt_tokens": (usage or {}).get("prompt_tokens", 0),
            "completion_tokens": (usage or {}).get("completion_tokens", 0),
            "latency": round(time.perf_counter() - t0, 4),
            "ttft": round(ttft, 4) if ttft is not None else None,
            "verdict": verdict,
            "reason": reason,
            "error": error,
        }
        store.add_run_item(run_id, item, _safe_request(payload))
        summary = {
            "actual_input_mode": actual_mode,
            "request_size_bytes": request_size_bytes,
            "request_size_warning": (
                f"请求体约 {request_size_bytes / 1024 / 1024:.1f} MB"
                if request_size_bytes > MM_INLINE_WARN_BYTES else ""
            ),
            "finish_reason": _finish_reason_hint(body),
            "output_preview": text[:500],
        }
        store.update_run(
            run_id, status="finished", verdict=verdict,
            summary_json=json.dumps(summary, ensure_ascii=False),
            finished_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        )
        _set_progress(run_id, status="finished", done=1, current="完成", verdict=verdict)
    except asyncio.CancelledError:
        store.update_run(run_id, status="canceled", error="用户中止",
                         finished_at=datetime.now().astimezone().isoformat(timespec="seconds"))
        _set_progress(run_id, status="canceled", error="用户中止")
    except Exception as exc:  # noqa: BLE001
        store.update_run(run_id, status="failed", error=str(exc),
                         finished_at=datetime.now().astimezone().isoformat(timespec="seconds"))
        _set_progress(run_id, status="failed", error=str(exc))
    finally:
        _tasks.pop(run_id, None)


def start_run(*, model_id: int | None, model_label: str, model: str, conn: dict,
              media: dict, input_mode: str, prompt: str, expected_keywords: list[str],
              config: dict, name: str = "") -> int:
    run_id = store.create_run(
        name=name or f"{model_label} · {media['name']}",
        model_id=model_id,
        model_label=model_label,
        model=model,
        connection_id=conn.get("id"),
        media_id=media["id"],
        media_kind=media["kind"],
        input_mode=input_mode,
        prompt=prompt,
        expected_keywords=expected_keywords,
        config=config,
    )
    _tasks[run_id] = asyncio.create_task(_execute(
        run_id=run_id,
        conn=conn,
        model_row={"model": model, "_extra_params": config.get("extra_params") or {}},
        media=media,
        input_mode=input_mode,
        prompt=prompt,
        expected=expected_keywords,
        config=config,
    ))
    return run_id


def cancel(run_id: int) -> bool:
    task = _tasks.get(run_id)
    if task is None or task.done():
        return False
    task.cancel()
    return True


def recover_stale_runs() -> None:
    store.mark_stale_runs()


async def shutdown() -> None:
    """应用关闭时取消仍在运行的多模态请求。"""
    tasks = [task for task in _tasks.values() if not task.done()]
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    _tasks.clear()
