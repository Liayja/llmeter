"""单次路由探测请求：采集响应头、usage、模型标识和错误阶段。"""
import hashlib
import json
import time
import uuid

import httpx

from ..key_store import resolve
from ..runner_bridge import build_chat_payload, build_chat_url, normalize_extra_params

MAX_RAW = 30000
def _error_phase(exc: Exception) -> str:
    name = type(exc).__name__.lower()
    if "connect" in name or "pool" in name:
        return "connect"
    if "write" in name:
        return "write"
    if "read" in name or "protocol" in name or "remote" in name:
        return "read"
    return "unknown"


def _headers(headers: httpx.Headers) -> dict:
    out = {}
    for key, value in headers.items():
        if key.lower() == "set-cookie":
            continue
        out[key] = value[:500] if len(value) > 500 else value
    return out


def _remote_ip(response: httpx.Response) -> str:
    try:
        stream = response.extensions.get("network_stream")
        if stream is not None:
            addr = stream.get_extra_info("server_addr")
            if addr:
                return str(addr[0])
    except Exception:      # noqa: BLE001
        pass
    return ""


def _model_fields(body: dict) -> tuple[str, str]:
    if not isinstance(body, dict):
        return "", ""
    return str(body.get("model") or ""), str(body.get("system_fingerprint") or "")


def _id_prefix(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    return value.split("-", 1)[0] if "-" in value else value[:10]


def _hash_json(value) -> str:
    if value is None:
        return ""
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def execute_attempt(*, client: httpx.AsyncClient, base_url: str, endpoint: str | None,
                          api_key: str, model: str, prompt: str, stream: bool,
                          max_tokens: int, temperature: float | None, timeout: float,
                          extra_params: dict | None, concurrency: int, sequence: int,
                          unique_prefix: bool, logprobs: bool = False) -> dict:
    actual_prompt = prompt
    if unique_prefix:
        actual_prompt = f"[route-probe-{uuid.uuid4().hex}]\n{prompt}"
    extra, _notes = normalize_extra_params(extra_params)
    payload = build_chat_payload(
        model=model, prompt=actual_prompt, stream=stream,
        max_tokens=max_tokens, temperature=temperature, extra_params=extra,
    )
    if logprobs:
        payload["logprobs"] = True
        payload["top_logprobs"] = 5
    client_request_id = f"llmeter-route-{uuid.uuid4().hex}"
    headers = {
        "Content-Type": "application/json",
        "X-Client-Request-Id": client_request_id,
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    url = build_chat_url(base_url, endpoint)
    t0 = time.perf_counter()
    status_code: int | None = None
    response_received = False
    response_headers: dict = {}
    remote_ip = ""
    http_version = ""
    ttfe_ms = None
    ttft_ms = None
    text = ""
    raw = ""
    raw_truncated = False
    usage: dict = {}
    response_model = ""
    system_fingerprint = ""
    response_id = ""
    id_prefix = ""
    finish_reason = ""
    logprobs_parts = []
    error = ""
    error_type = ""
    error_phase = ""
    timeout_cfg = httpx.Timeout(connect=min(timeout, 10.0), read=timeout,
                                write=min(timeout, 10.0), pool=min(timeout, 10.0))
    try:
        if stream:
            async with client.stream("POST", url, json=payload, headers=headers,
                                     timeout=timeout_cfg) as response:
                status_code = response.status_code
                response_received = True
                response_headers = _headers(response.headers)
                remote_ip = _remote_ip(response)
                http_version = getattr(response, "http_version", "") or ""
                parts: list[str] = []
                raw_lines: list[str] = []
                async for line in response.aiter_lines():
                    if len(raw_lines) < 300:
                        raw_lines.append(line)
                    else:
                        raw_truncated = True
                    if ttfe_ms is None:
                        ttfe_ms = round((time.perf_counter() - t0) * 1000, 3)
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
                    if chunk.get("id"):
                        response_id = str(chunk.get("id"))
                        id_prefix = _id_prefix(response_id)
                    if chunk.get("model"):
                        response_model = str(chunk.get("model") or "")
                    if chunk.get("system_fingerprint"):
                        system_fingerprint = str(chunk.get("system_fingerprint") or "")
                    choice = (chunk.get("choices") or [{}])[0] or {}
                    if choice.get("logprobs"):
                        logprobs_parts.append(choice["logprobs"])
                    if choice.get("finish_reason"):
                        finish_reason = str(choice.get("finish_reason"))
                    delta = choice.get("delta") or {}
                    content = delta.get("content") or ""
                    if content:
                        if ttft_ms is None:
                            ttft_ms = round((time.perf_counter() - t0) * 1000, 3)
                        parts.append(content)
                text = "".join(parts)
                raw = "\n".join(raw_lines)
        else:
            response = await client.post(url, json=payload, headers=headers, timeout=timeout_cfg)
            status_code = response.status_code
            response_received = True
            response_headers = _headers(response.headers)
            remote_ip = _remote_ip(response)
            http_version = getattr(response, "http_version", "") or ""
            raw = response.text
            try:
                body = response.json()
            except Exception:      # noqa: BLE001
                body = None
            if isinstance(body, dict):
                usage = body.get("usage") or {}
                response_id = str(body.get("id") or "")
                id_prefix = _id_prefix(response_id)
                response_model, system_fingerprint = _model_fields(body)
                choice = (body.get("choices") or [{}])[0] or {}
                if choice.get("logprobs"):
                    logprobs_parts.append(choice["logprobs"])
                text = (choice.get("message") or {}).get("content") or ""
                finish_reason = str(choice.get("finish_reason") or "")
            else:
                text = response.text
    except Exception as exc:      # noqa: BLE001
        error_type = type(exc).__name__
        error_phase = _error_phase(exc)
        error = f"{error_type}: {str(exc) or repr(exc)}"
    raw_truncated = raw_truncated or len(raw) > MAX_RAW
    raw = raw[:MAX_RAW]
    output_hash = hashlib.sha256(text.encode("utf-8")).hexdigest() if text else ""
    logprobs_hash = _hash_json(logprobs_parts) if logprobs_parts else ""
    return {
        "sequence": sequence,
        "concurrency": concurrency,
        "client_request_id": client_request_id,
        "phase": "stream" if stream else "nonstream",
        "status_code": status_code,
        "error_type": error_type,
        "error_phase": error_phase,
        "response_received": response_received,
        "remote_ip": remote_ip,
        "http_version": http_version,
        "ttfe_ms": ttfe_ms,
        "ttft_ms": ttft_ms,
        "latency_ms": round((time.perf_counter() - t0) * 1000, 3),
        "request_size_bytes": len(json.dumps(payload, ensure_ascii=False).encode("utf-8")),
        "response_headers": response_headers,
        "response_model": response_model,
        "system_fingerprint": system_fingerprint,
        "response_id": response_id,
        "id_prefix": id_prefix,
        "usage": usage,
        "prompt_tokens": usage.get("prompt_tokens", 0),
        "completion_tokens": usage.get("completion_tokens", 0),
        "finish_reason": finish_reason,
        "output_hash": output_hash,
        "logprobs_hash": logprobs_hash,
        "logprobs_available": bool(logprobs_parts),
        "request": payload,
        "raw_response": raw,
        "raw_response_truncated": raw_truncated,
        "error": error,
    }
