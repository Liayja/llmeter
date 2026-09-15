"""密钥存取：环境变量引用 或 本机加密（Windows DPAPI）。

- env 模式：库里只存变量名，运行时展开 ${VAR}；
- local 模式：库里存密文，运行时解密，接口只返回脱敏值；
- 非 Windows 平台降级为 base64 存储（启动时会提示，仅用于本地开发）。
"""
import base64
import ctypes
import os
import sys
from ctypes import wintypes


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi(data: bytes, protect: bool) -> bytes:
    """调用 Windows DPAPI。protect=True 加密，False 解密。"""
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    # 注意：缓冲区必须保持引用，否则 cast 之后可能被 GC 回收导致指针失效
    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = _DataBlob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = _DataBlob()

    CryptProtectData = crypt32.CryptProtectData
    CryptProtectData.restype = wintypes.BOOL
    CryptProtectData.argtypes = [
        ctypes.POINTER(_DataBlob), wintypes.LPCWSTR, ctypes.POINTER(_DataBlob),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DataBlob),
    ]
    CryptUnprotectData = crypt32.CryptUnprotectData
    CryptUnprotectData.restype = wintypes.BOOL
    CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DataBlob), ctypes.c_void_p, ctypes.POINTER(_DataBlob),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DataBlob),
    ]
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p

    fn = CryptProtectData if protect else CryptUnprotectData
    ok = fn(ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out))
    if not ok:
        raise OSError(f"DPAPI 调用失败，错误码 {ctypes.GetLastError()}")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def is_windows() -> bool:
    return os.name == "nt"


def protect(secret: str) -> str:
    """把明文密钥转成可入库的字符串。"""
    raw = secret.encode("utf-8")
    if is_windows():
        return "dpapi:" + base64.b64encode(_dpapi(raw, True)).decode("ascii")
    return "b64:" + base64.b64encode(raw).decode("ascii")


def unprotect(stored: str) -> str:
    """把库里的密文还原成明文密钥。"""
    if not stored:
        return ""
    if stored.startswith("dpapi:"):
        return _dpapi(base64.b64decode(stored[6:]), False).decode("utf-8")
    if stored.startswith("b64:"):
        return base64.b64decode(stored[4:]).decode("utf-8")
    return stored  # 兼容历史明文


def mask(secret: str) -> str:
    """脱敏显示：sk-****abcd。"""
    if not secret:
        return ""
    tail = secret[-4:] if len(secret) > 8 else ""
    return f"{secret[:3]}****{tail}"


def resolve(connection: dict) -> str:
    """从连接配置取出运行时使用的明文密钥。"""
    mode = (connection.get("key_mode") or "env").lower()
    ref = connection.get("key_ref") or ""
    if mode == "local":
        return unprotect(ref)
    # env 模式：允许直接写变量名（OMNIX_KEY）或 ${OMNIX_KEY}
    var = ref.strip()
    if var.startswith("${") and var.endswith("}"):
        var = var[2:-1]
    return os.environ.get(var, "") if var else ""


def fallback_warning() -> str | None:
    if is_windows():
        return None
    return "当前系统非 Windows，密钥以 base64 降级存储（仅建议本机开发使用）"
