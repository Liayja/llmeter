"""多模态媒体资产：本地 data URL 入库、公网 URL 注册和请求材料化。"""
import base64
import hashlib
import ipaddress
import json
from pathlib import Path
from urllib.parse import urlparse

import httpx

from ...settings import (
    MM_INLINE_WARN_BYTES,
    MM_MAX_INLINE_REQUEST_BYTES,
    MM_MAX_MEDIA_BYTES,
    MM_MEDIA_DIR,
)
from . import store

IMAGE_MIMES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
VIDEO_MIMES = {"video/mp4", "video/webm", "video/quicktime"}
_EXT = {
    "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp",
    "image/gif": ".gif", "video/mp4": ".mp4", "video/webm": ".webm",
    "video/quicktime": ".mov",
}


class MediaError(ValueError):
    """媒体格式、大小或来源不合法。"""


def base64_size(raw_size: int) -> int:
    """Base64 编码后的字符数。"""
    return 4 * ((raw_size + 2) // 3)


def inline_media_size(media: dict) -> int:
    """本地媒体的 data URL 字符数；远端未知时返回 0。"""
    size = int(media.get("size_bytes") or 0)
    if not size and media.get("local_path"):
        path = Path(media["local_path"])
        if path.exists():
            size = path.stat().st_size
    if not size:
        return 0
    mime = media.get("mime_type") or "application/octet-stream"
    return len(f"data:{mime};base64,") + base64_size(size)


def estimate_inline_request_bytes(media: dict, *, prompt: str = "",
                                  max_tokens: int = 512,
                                  extra_params: dict | None = None) -> int | None:
    """估算 inline 请求体的 JSON 体积；远端未下载时无法准确估算。"""
    media_size = inline_media_size(media)
    if not media_size:
        return None
    prompt_size = len((prompt or "").encode("utf-8"))
    extra_size = len(json.dumps(extra_params or {}, ensure_ascii=False).encode("utf-8"))
    # 消息结构、字段名、模型名和 JSON 转义预留约 1 KB。
    return media_size + prompt_size * 2 + extra_size + 1024 + max_tokens


def check_inline_request_size(media: dict, *, input_mode: str, prompt: str,
                              max_tokens: int, extra_params: dict | None,
                              allow_large: bool = False) -> tuple[int | None, str]:
    """本地 inline 请求的发送前检查，返回 (估算体积, 警告)。"""
    mode = (input_mode or "auto").lower()
    will_inline = mode == "inline" or (mode == "auto" and media.get("local_path"))
    if not will_inline:
        return None, ""
    estimate = estimate_inline_request_bytes(
        media, prompt=prompt, max_tokens=max_tokens, extra_params=extra_params,
    )
    if estimate is None:
        return None, ""
    if estimate > MM_MAX_INLINE_REQUEST_BYTES and not allow_large:
        raise MediaError(
            f"预计请求体约 {estimate / 1024 / 1024:.1f} MB，超过 "
            f"{MM_MAX_INLINE_REQUEST_BYTES / 1024 / 1024:.0f} MB 安全线。"
            "请改用公网 URL，或勾选确认后强制发送。"
        )
    warning = (
        f"预计请求体约 {estimate / 1024 / 1024:.1f} MB，Base64 会明显放大体积。"
        if estimate > MM_INLINE_WARN_BYTES else ""
    )
    return estimate, warning


def _kind_mimes(kind: str) -> set[str]:
    if kind == "image":
        return IMAGE_MIMES
    if kind == "video":
        return VIDEO_MIMES
    raise MediaError("媒体类型必须是 image 或 video")


def _decode_data_url(data_url: str) -> tuple[str, bytes]:
    if not data_url.startswith("data:") or ";base64," not in data_url:
        raise MediaError("本地文件必须是 data:<mime>;base64,... 格式")
    header, payload = data_url.split(",", 1)
    mime = header[5:].split(";", 1)[0].lower()
    try:
        data = base64.b64decode(payload, validate=True)
    except Exception as exc:      # noqa: BLE001
        raise MediaError("文件 Base64 数据损坏") from exc
    if not data:
        raise MediaError("文件内容为空")
    if len(data) > MM_MAX_MEDIA_BYTES:
        raise MediaError(f"文件超过 {MM_MAX_MEDIA_BYTES // 1024 // 1024} MB 限制")
    return mime, data


def _sniff_mime(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    if len(data) > 12 and data[4:8] == b"ftyp":
        return "video/mp4"
    if data.startswith(b"\x1a\x45\xdf\xa3"):
        return "video/webm"
    if data.startswith(b"\x00\x00\x00") and data[4:8] in (b"ftyp", b"moov", b"mdat"):
        return "video/quicktime"
    return ""


def _ensure_remote_url(url: str) -> str:
    value = (url or "").strip()
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise MediaError("公网 URL 必须是完整的 http:// 或 https:// 地址")
    host = parsed.hostname.lower()
    if host in ("localhost", "localhost.localdomain"):
        raise MediaError("不允许使用 localhost，请填写公网可访问地址")
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_private or ip.is_loopback or ip.is_link_local:
            raise MediaError("不允许使用内网或回环地址")
    except ValueError:
        pass
    return value


def save_local(*, name: str, kind: str, data_url: str) -> dict:
    declared_mime, data = _decode_data_url(data_url)
    allowed = _kind_mimes(kind)
    if declared_mime not in allowed:
        raise MediaError(f"{kind} 不支持该 MIME：{declared_mime}")
    sniffed = _sniff_mime(data)
    if sniffed and sniffed != declared_mime:
        raise MediaError(f"文件内容看起来是 {sniffed}，与声明的 {declared_mime} 不一致")
    digest = hashlib.sha256(data).hexdigest()
    existing = store.find_media_by_sha(digest)
    if existing:
        return existing
    target_dir = MM_MEDIA_DIR / digest[:2]
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{digest}{_EXT.get(declared_mime, '')}"
    target.write_bytes(data)
    media_id = store.create_media(
        name=(name or Path(target).name).strip(),
        kind=kind,
        source_type="local",
        mime_type=declared_mime,
        size_bytes=len(data),
        sha256=digest,
        local_path=str(target),
        metadata={"extension": target.suffix.lower()},
    )
    return store.get_media(media_id)


def register_remote(*, name: str, kind: str, url: str) -> dict:
    _kind_mimes(kind)
    value = _ensure_remote_url(url)
    media_id = store.create_media(
        name=(name or value).strip(),
        kind=kind,
        source_type="remote",
        remote_url=value,
        metadata={"host": urlparse(value).hostname},
    )
    return store.get_media(media_id)


async def _fetch_remote(media: dict) -> tuple[str, bytes]:
    url = media.get("remote_url") or ""
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        response = await client.get(url)
        response.raise_for_status()
        data = response.content
    if len(data) > MM_MAX_MEDIA_BYTES:
        raise MediaError(f"远端文件超过 {MM_MAX_MEDIA_BYTES // 1024 // 1024} MB 限制")
    mime = (response.headers.get("content-type") or media.get("mime_type") or "").split(";")[0]
    if not mime:
        raise MediaError("远端响应没有 Content-Type，无法判断文件类型")
    return mime, data


async def materialize(media: dict, input_mode: str = "auto") -> tuple[str, str]:
    """返回 (发送给模型的 URL/data URL, 实际使用方式)。"""
    mode = (input_mode or "auto").lower()
    if mode not in ("auto", "url", "inline"):
        raise MediaError("input_mode 必须是 auto、url 或 inline")
    has_local = bool(media.get("local_path"))
    if mode == "url":
        if not media.get("remote_url"):
            raise MediaError("本地文件无法直接作为公网 URL 发送，请改用 inline")
        return media["remote_url"], "url"
    if mode == "inline" or (mode == "auto" and has_local):
        if has_local:
            path = Path(media["local_path"])
            data = path.read_bytes()
            mime = media.get("mime_type") or "application/octet-stream"
        else:
            mime, data = await _fetch_remote(media)
        return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}", "inline"
    if not media.get("remote_url"):
        raise MediaError("媒体没有可用的 URL")
    return media["remote_url"], "url"
