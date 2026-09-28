"""多模态 Chat Completions 请求体构造。"""
from ..runner_bridge import normalize_extra_params

_STRUCTURAL_PARAMS = {
    "model", "messages", "stream", "stream_options", "max_tokens",
    "max_completion_tokens", "temperature", "top_p", "top_k", "seed", "n",
}


def build_payload(*, model: str, kind: str, media_ref: str, prompt: str,
                  stream: bool, max_tokens: int, temperature: float | None,
                  extra_params: dict | None) -> dict:
    extra, _notes = normalize_extra_params(extra_params)
    safe_extra = {k: v for k, v in extra.items() if k not in _STRUCTURAL_PARAMS}
    if kind == "image":
        media_part = {"type": "image_url", "image_url": {"url": media_ref}}
    elif kind == "video":
        media_part = {"type": "video_url", "video_url": {"url": media_ref}}
    else:
        raise ValueError("只支持 image 或 video")
    payload = {
        **safe_extra,
        "model": model,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt or "请描述这个媒体的内容。"},
                media_part,
            ],
        }],
        "max_tokens": int(max_tokens),
        "stream": bool(stream),
    }
    if temperature is not None:
        payload["temperature"] = temperature
    if stream:
        payload["stream_options"] = {"include_usage": True}
    return payload
