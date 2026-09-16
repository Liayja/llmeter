"""执行单个一致性用例：完全控制请求体，采集输出文本 / usage / 行为类别。"""
import json
import time

import httpx

from ..runner_bridge import build_chat_url, normalize_extra_params

# 行为类别（用于 D9 协议健壮性判定：不比文本，比行为是否同类）
BEHAVIOR_REJECT = "REJECT"            # 4xx：明确拒绝
BEHAVIOR_ACCEPT = "ACCEPT_AS_IS"      # 200：接受
BEHAVIOR_ERROR = "ERROR_OTHER"        # 5xx：服务异常
BEHAVIOR_UNKNOWN = "UNKNOWN"          # 异常/无响应

MAX_TEXT = 8000


def classify(status_code: int | None, error: str = "") -> str:
    if error or status_code is None:
        return BEHAVIOR_UNKNOWN
    if 400 <= status_code < 500:
        return BEHAVIOR_REJECT
    if status_code == 200:
        return BEHAVIOR_ACCEPT
    if status_code >= 500:
        return BEHAVIOR_ERROR
    return BEHAVIOR_UNKNOWN


def _pick_text_nonstream(body: dict) -> tuple[str, list]:
    choices = body.get("choices") or [{}]
    msg = (choices[0] or {}).get("message") or {}
    return (msg.get("content") or ""), (msg.get("tool_calls") or [])


async def execute_case(*, base_url: str, endpoint: str | None, api_key: str, model: str,
                       case: dict, extra_params: dict | None = None,
                       timeout: float = 60.0) -> dict:
    payload = {"model": model, **case.get("payload", {})}
    payload.setdefault("stream", False)
    extra, _notes = normalize_extra_params(extra_params)
    if extra:
        payload.update(extra)

    url = build_chat_url(base_url, endpoint)
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    t0 = time.perf_counter()
    status = None
    usage: dict = {}
    text = ""
    tool_calls: list = []
    error = ""
    raw_response = ""
    ttft = None
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            if payload.get("stream"):
                parts: list[str] = []
                async with client.stream("POST", url, json=payload, headers=headers) as r:
                    status = r.status_code
                    raw_lines: list[str] = []
                    async for line in r.aiter_lines():
                        if len(raw_lines) < 200:
                            raw_lines.append(line)
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
                        ch = (chunk.get("choices") or [{}])[0] or {}
                        content = (ch.get("delta") or {}).get("content") or ""
                        if content:
                            if ttft is None:
                                ttft = time.perf_counter() - t0
                            parts.append(content)
                text = "".join(parts)
                raw_response = "\n".join(raw_lines)[:20000]
            else:
                r = await client.post(url, json=payload, headers=headers)
                status = r.status_code
                raw_response = r.text[:20000]      # 原始响应体（4xx 的报错体也在这里）
                try:
                    body = r.json()
                except Exception:      # noqa: BLE001
                    body = None
                if isinstance(body, dict):
                    usage = body.get("usage") or {}
                    text, tool_calls = _pick_text_nonstream(body)
                else:
                    text = r.text
    except Exception as e:      # noqa: BLE001
        error = f"{type(e).__name__}: {e}"

    return {
        "case_id": case["id"],
        "dimension": case["dimension"],
        "status_code": status,
        "behavior_class": classify(status, error),
        "usage": usage or {},
        "prompt_tokens": (usage or {}).get("prompt_tokens", 0),
        "completion_tokens": (usage or {}).get("completion_tokens", 0),
        "output_text": (text or "")[:MAX_TEXT],
        "raw_response": raw_response,
        "tool_calls": tool_calls,
        "error": error,
        "latency": round(time.perf_counter() - t0, 4),
        "ttft": round(ttft, 4) if ttft is not None else None,
    }
