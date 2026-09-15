"""连通性测试 / 响应结构探测。

用**与压测完全相同的请求体构造**发一次真实请求，回显：
- 请求：URL、脱敏后的请求头、完整 payload（就是压测会发的那个体）
- 响应：分两类字段（UI 里分开显示，避免把客户端测量值误当成接口字段）——
  原生：status_code / headers / raw（HTTP 原文）/ usage / chunks（原始分片）；
  客户端计算：elapsed / ttft / chunk_count / text / reasoning_text
- 提示：usage 是否齐全、有没有 cached_tokens / reasoning_tokens、流式怎么解析
目的是让用户在正式压测前，先确认模型名、extra_params、返回结构都符合预期。
"""
import json
import time

import httpx

from .runner_bridge import (build_chat_payload, build_chat_url, count_tokens,
                            normalize_extra_params)

# 值得保留的响应头（限流信息对压测很有用）
INTERESTING_HEADERS = (
    "content-type", "retry-after", "server", "date",
    "x-ratelimit-limit-requests", "x-ratelimit-remaining-requests",
    "x-ratelimit-limit-tokens", "x-ratelimit-remaining-tokens",
    "x-request-id", "request-id",
)


def _mask_auth(headers: dict) -> dict:
    out = dict(headers)
    auth = out.get("Authorization")
    if auth:
        out["Authorization"] = auth[:10] + "****"
    return out


def _pick_headers(headers: httpx.Headers) -> dict:
    keep = {}
    for k, v in headers.items():
        if k.lower() in INTERESTING_HEADERS:
            keep[k] = v
    return keep


def _pick_content(chunk: dict) -> tuple[str, str]:
    """从 SSE chunk 里取 (答案内容, 推理内容)，兼容多种字段格式。"""
    choices = chunk.get("choices") or [{}]
    msg = choices[0] if choices else {}
    msg = msg or {}
    delta = msg.get("delta") or {}
    content = delta.get("content") or delta.get("text") or ""
    if not content:
        content = (msg.get("message") or {}).get("content") or ""
    reasoning = delta.get("reasoning_content") or ""
    return content, reasoning


async def probe(*, base_url: str, endpoint: str | None, api_key: str, model: str,
                prompt: str, max_tokens: int = 32, temperature: float | None = None,
                extra_params: dict | None = None, stream: bool = False,
                timeout: float = 30.0) -> dict:
    url = build_chat_url(base_url, endpoint)
    # 数字字符串（如 "max_tokens": "256"）多数网关会忽略 → 先规范化为数字并提示
    extra_params, param_notes = normalize_extra_params(extra_params)
    payload = build_chat_payload(model=model, prompt=prompt, stream=stream,
                                 max_tokens=max_tokens, temperature=temperature,
                                 extra_params=extra_params)
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    result = {
        "request": {
            "method": "POST",
            "url": url,
            "headers": _mask_auth(headers),
            "body": payload,
            "prompt_tokens_est": count_tokens(prompt),
        },
        "response": {"stream": stream},
        "hints": [],
    }
    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            if stream:
                chunks, text_parts, reasoning_parts = [], [], []
                raw_lines: list[str] = []
                ttft = None
                usage = None
                status = None
                resp_headers: dict = {}
                async with client.stream("POST", url, json=payload, headers=headers) as r:
                    status = r.status_code
                    resp_headers = _pick_headers(r.headers)
                    async for line in r.aiter_lines():
                        if len(raw_lines) < 60:
                            raw_lines.append(line)      # 保留 SSE 原文
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if not data:
                            continue
                        if data == "[DONE]":
                            chunks.append("[DONE]")
                            break
                        try:
                            chunk = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        if len(chunks) < 20:
                            chunks.append(chunk)
                        if chunk.get("usage"):
                            usage = chunk["usage"]
                        content, reasoning = _pick_content(chunk)
                        if (content or reasoning) and ttft is None:
                            ttft = time.perf_counter() - t0
                        if content:
                            text_parts.append(content)
                        if reasoning:
                            reasoning_parts.append(reasoning)
                result["response"].update({
                    "status_code": status,
                    "headers": resp_headers,
                    "elapsed": round(time.perf_counter() - t0, 4),
                    "ttft": round(ttft, 4) if ttft is not None else None,
                    "usage": usage,
                    "raw": "\n".join(raw_lines)[:8000],
                    "raw_truncated": len(raw_lines) >= 60,
                    "chunk_count": len(chunks),
                    "chunks": chunks[:20],
                    "text": "".join(text_parts)[:2000],
                    "reasoning_text": "".join(reasoning_parts)[:1000],
                })
            else:
                r = await client.post(url, json=payload, headers=headers)
                elapsed = time.perf_counter() - t0
                parsed = None
                try:
                    parsed = r.json()
                except Exception:      # noqa: BLE001
                    parsed = None
                result["response"].update({
                    "status_code": r.status_code,
                    "headers": _pick_headers(r.headers),
                    "elapsed": round(elapsed, 4),
                    "ttft": None,
                    "usage": parsed.get("usage") if isinstance(parsed, dict) else None,
                    "raw": r.text[:8000],               # HTTP 原文，未经加工
                    "raw_truncated": len(r.text) > 8000,
                    "body": parsed if parsed is not None else r.text[:4000],
                    "body_is_json": parsed is not None,
                })
    except Exception as e:      # noqa: BLE001
        result["response"].update({
            "error": f"{type(e).__name__}: {e}",
            "elapsed": round(time.perf_counter() - t0, 4),
        })
        result["hints"] = ["请求本身失败：检查 Base URL / 接口路径 / 网络 / 代理设置"]
        return result

    result["hints"] = param_notes + _analyze(result)
    # extra_params 覆盖告警：这是"配置了 max_tokens 但实际没生效"的最常见原因
    if extra_params:
        base = build_chat_payload(model=model, prompt=prompt, stream=stream,
                                  max_tokens=max_tokens, temperature=temperature)
        conflicts = [f"{k}：{base[k]} → {extra_params[k]}"
                     for k in extra_params
                     if k in base and base[k] != extra_params[k]]
        if conflicts:
            result["hints"].insert(
                0, "⚠️ extra_params 覆盖了这些参数（实际发出去的是覆盖后的值）：" + "；".join(conflicts))
    return result


def _analyze(result: dict) -> list[str]:
    hints: list[str] = []
    resp = result["response"]
    status = resp.get("status_code")
    usage = resp.get("usage")

    if status and status != 200:
        hints.append(f"⚠️ HTTP {status}：请求未成功，先按错误信息修正（401 密钥 / 404 路径 / 429 限流）")
        return hints
    hints.append(f"✅ HTTP 200，耗时 {resp.get('elapsed')}s")

    # ── usage 与 token 口径 ──
    if isinstance(usage, dict):
        hints.append(f"usage 正常：prompt {usage.get('prompt_tokens')} / completion "
                     f"{usage.get('completion_tokens')}（压测 token 统计会用它）")
        details = usage.get("prompt_tokens_details") or {}
        if "cached_tokens" in details:
            hints.append(f"✅ 支持缓存统计：cached_tokens={details.get('cached_tokens')}"
                         "（压测可勾选「统计缓存命中」）")
        else:
            hints.append("⚠️ 未返回 prompt_tokens_details.cached_tokens"
                         "（压测里「缓存命中」列会显示 —）")
        c_details = usage.get("completion_tokens_details") or {}
        if c_details.get("reasoning_tokens") is not None:
            hints.append(f"含推理 token：reasoning_tokens={c_details.get('reasoning_tokens')}")
        requested = (result.get("request", {}).get("body") or {}).get("max_tokens")
        completion = usage.get("completion_tokens")
        if isinstance(requested, (int, float)) and isinstance(completion, (int, float)) \
                and completion > requested:
            hints.append(f"⚠️ 服务端未遵守 max_tokens：请求上限 {requested}，实际输出 {completion} token"
                         "（网关可能忽略该参数或使用默认上限，建议向服务商确认）")
    else:
        hints.append("⚠️ 响应没有 usage → 压测里 token 数会退化为本地估算（TPS 误差变大）")

    # ── 正文结构 ──
    if resp.get("stream"):
        if resp.get("ttft") is not None:
            hints.append(f"✅ 流式正常：TTFT {resp['ttft']}s（客户端测量：发请求→首个内容分片），"
                         f"共 {resp.get('chunk_count')} 个分片")
        text = resp.get("text") or ""
        if text:
            hints.append(f"解析方式：choices[0].delta.content（预览：{text[:40]!r}）")
        elif resp.get("reasoning_text"):
            hints.append("只收到 reasoning_content：该模型先输出思考内容，答案仍会单独给出")
        else:
            hints.append("⚠️ 未解析出内容：请检查上面的原始分片格式")
    else:
        body = resp.get("body")
        if isinstance(body, dict):
            choices = body.get("choices") or []
            if choices:
                msg = (choices[0] or {}).get("message") or {}
                if msg.get("content") is not None:
                    hints.append("响应结构：choices[0].message.content ✅（非流式标准格式）")
                if msg.get("reasoning_content"):
                    hints.append("响应含 reasoning_content（推理内容）")
            else:
                hints.append("⚠️ 响应里没有 choices：可能是非标准网关，建议改看原始响应体")
        else:
            hints.append("⚠️ 响应不是 JSON：检查接口路径是否指到了网页/网关")
    return hints
