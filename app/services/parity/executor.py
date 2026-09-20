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
MAX_RAW = 100000

# 这些字段属于用例协议本身，不能被模型级 extra_params 覆盖或补入，
# 否则不同用例可能实际发出的请求体不再一致，结果不可复现。
_STRUCTURAL_PARAMS = {
    "model", "messages", "tools", "tool_choice", "parallel_tool_calls",
    "response_format", "stream", "stream_options", "max_tokens",
    "max_completion_tokens", "temperature", "top_p", "top_k", "seed", "n",
    "stop", "presence_penalty", "frequency_penalty", "logprobs", "top_logprobs",
}


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
    case_payload = case.get("payload", {})
    extra, _notes = normalize_extra_params(extra_params)
    protected = _STRUCTURAL_PARAMS | set(case_payload)
    safe_extra = {k: v for k, v in extra.items() if k not in protected}
    ignored = sorted(k for k in extra if k in protected)
    payload = {**safe_extra, **case_payload, "model": model}
    payload.setdefault("stream", False)

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
    raw_response_truncated = False
    output_text_truncated = False
    ttft = None
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            if payload.get("stream"):
                parts: list[str] = []
                raw_lines_truncated = False
                async with client.stream("POST", url, json=payload, headers=headers) as r:
                    status = r.status_code
                    raw_lines: list[str] = []
                    async for line in r.aiter_lines():
                        if len(raw_lines) < 200:
                            raw_lines.append(line)
                        else:
                            raw_lines_truncated = True
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
                raw_response = "\n".join(raw_lines)
                raw_response_truncated = raw_lines_truncated or len(raw_response) > MAX_RAW
                raw_response = raw_response[:MAX_RAW]
            else:
                r = await client.post(url, json=payload, headers=headers)
                status = r.status_code
                raw_response = r.text
                raw_response_truncated = len(raw_response) > MAX_RAW
                raw_response = raw_response[:MAX_RAW]  # 4xx 的报错体也保留
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

    output_text = text or ""
    output_text_truncated = len(output_text) > MAX_TEXT
    param_notes = list(_notes)
    if ignored:
        param_notes.insert(
            0, f"模型级 extra_params 中的结构字段未应用：{', '.join(ignored)}"
        )
    return {
        "case_id": case["id"],
        "dimension": case["dimension"],
        "status_code": status,
        "behavior_class": classify(status, error),
        "usage": usage or {},
        "prompt_tokens": (usage or {}).get("prompt_tokens", 0),
        "completion_tokens": (usage or {}).get("completion_tokens", 0),
        "output_text": output_text[:MAX_TEXT],
        "raw_response": raw_response,
        "raw_response_truncated": raw_response_truncated,
        "output_text_truncated": output_text_truncated,
        "tool_calls": tool_calls,
        "param_notes": param_notes,
        "error": error,
        "latency": round(time.perf_counter() - t0, 4),
        "ttft": round(ttft, 4) if ttft is not None else None,
    }
