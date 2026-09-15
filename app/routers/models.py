"""模型管理 API。"""
import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..services import catalog, key_store
from ..services import probe as probe_service

router = APIRouter(prefix="/api/models", tags=["models"])


class ModelIn(BaseModel):
    label: str
    model: str
    connection_id: int | None = None
    extra_params: dict | None = None
    note: str = ""


@router.get("")
def list_models() -> list[dict]:
    return catalog.list_models()


@router.post("")
def create_model(payload: ModelIn) -> dict:
    if payload.connection_id and not catalog.get_connection(payload.connection_id):
        raise HTTPException(400, "指定的连接不存在")
    new_id = catalog.create_model(payload.label, payload.model, payload.connection_id,
                                 payload.extra_params, payload.note)
    return catalog.get_model(new_id)


@router.put("/{model_id}")
def update_model(model_id: int, payload: ModelIn) -> dict:
    if not catalog.get_model(model_id):
        raise HTTPException(404, "模型不存在")
    catalog.update_model(model_id, payload.label, payload.model, payload.connection_id,
                        payload.extra_params, payload.note)
    return catalog.get_model(model_id)


@router.delete("/{model_id}")
def delete_model(model_id: int) -> dict:
    if not catalog.get_model(model_id):
        raise HTTPException(404, "模型不存在")
    catalog.delete_model(model_id)
    return {"ok": True}


class ProbeIn(BaseModel):
    prompt: str = "ping"
    max_tokens: int = 32
    stream: bool = False
    temperature: float | None = None
    extra_params: dict | None = None      # 不填则用模型自带的配置


@router.post("/{model_id}/probe")
async def probe_model(model_id: int, payload: ProbeIn) -> dict:
    """连通性测试 + 请求/响应结构回显（用与压测完全一致的请求体构造）。"""
    model_row = catalog.get_model(model_id)
    if not model_row:
        raise HTTPException(404, "模型不存在")
    conn = catalog.get_connection(model_row["connection_id"]) if model_row.get("connection_id") else None
    if not conn:
        raise HTTPException(400, "该模型没有绑定连接，请先在资产库补齐")
    extra = payload.extra_params
    if extra is None:
        extra = json.loads(model_row.get("extra_params") or "{}") or None
    return await probe_service.probe(
        base_url=conn["base_url"], endpoint=conn.get("endpoint") or None,
        api_key=key_store.resolve(conn), model=model_row["model"],
        prompt=payload.prompt, max_tokens=payload.max_tokens,
        temperature=payload.temperature, extra_params=extra,
        stream=payload.stream, timeout=60.0,
    )
