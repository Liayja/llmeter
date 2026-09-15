"""连接管理 API。"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..services import catalog, key_store
from ..services import probe as probe_service

router = APIRouter(prefix="/api/connections", tags=["connections"])


class ConnectionIn(BaseModel):
    name: str
    base_url: str
    endpoint: str = ""             # 空=默认 /v1/chat/completions；智谱填 /chat/completions
    key_mode: str = "env"          # env | local
    env_var: str = ""              # key_mode=env 时使用
    api_key: str = ""              # key_mode=local 时使用（仅写入，不回显）
    note: str = ""


def _public(row: dict) -> dict:
    """对外只暴露脱敏后的密钥信息。"""
    mode = row.get("key_mode") or "env"
    if mode == "env":
        shown = row.get("key_ref") or ""
    else:
        try:
            shown = key_store.mask(key_store.unprotect(row.get("key_ref") or ""))
        except Exception:
            shown = "****"
    return {**row, "key_ref": shown, "has_secret": bool(row.get("key_ref"))}


@router.get("")
def list_connections() -> list[dict]:
    return [_public(r) for r in catalog.list_connections()]


@router.post("")
def create_connection(payload: ConnectionIn) -> dict:
    key_ref = payload.env_var if payload.key_mode == "env" else key_store.protect(payload.api_key)
    new_id = catalog.create_connection(payload.name, payload.base_url.rstrip("/"),
                                      payload.key_mode, key_ref, payload.note,
                                      payload.endpoint.strip())
    return _public(catalog.get_connection(new_id))


@router.put("/{conn_id}")
def update_connection(conn_id: int, payload: ConnectionIn) -> dict:
    old = catalog.get_connection(conn_id)
    if not old:
        raise HTTPException(404, "连接不存在")
    if payload.key_mode == "env":
        key_ref = payload.env_var or old.get("key_ref", "")
    else:
        key_ref = key_store.protect(payload.api_key) if payload.api_key else old.get("key_ref", "")
    catalog.update_connection(conn_id, payload.name, payload.base_url.rstrip("/"),
                             payload.key_mode, key_ref, payload.note, payload.endpoint.strip())
    return _public(catalog.get_connection(conn_id))


@router.delete("/{conn_id}")
def delete_connection(conn_id: int) -> dict:
    if not catalog.get_connection(conn_id):
        raise HTTPException(404, "连接不存在")
    catalog.delete_connection(conn_id)
    return {"ok": True}


@router.post("/{conn_id}/test")
async def test_connection(conn_id: int) -> dict:
    """连通性测试：发一个最小请求（非流式、max_tokens=1）。"""
    conn = catalog.get_connection(conn_id)
    if not conn:
        raise HTTPException(404, "连接不存在")
    api_key = key_store.resolve(conn)
    model = ""
    for m in catalog.list_models():
        if m.get("connection_id") == conn_id:
            model = m["model"]
            break
    if not model:
        raise HTTPException(400, "该连接下还没有模型，无法测试")

    result = await probe_service.probe(
        base_url=conn["base_url"], endpoint=conn.get("endpoint") or None,
        api_key=api_key, model=model, prompt="ping",
        max_tokens=1, stream=False, timeout=25.0,
    )
    resp = result["response"]
    ok = resp.get("status_code") == 200
    message = "连接正常" if ok else str(
        resp.get("error") or resp.get("body") or "请求失败")[:300]
    return {"ok": ok, "latency": resp.get("elapsed"),
            "status_code": resp.get("status_code"),
            "message": message, "hints": result.get("hints", [])}
