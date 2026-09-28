"""多模态能力测试 API。"""
import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel

from ..services import catalog
from ..services.multimodal import media as media_service
from ..services.multimodal import runner, store

router = APIRouter(prefix="/api/mm", tags=["multimodal"])


class LocalMediaIn(BaseModel):
    name: str = ""
    kind: str
    data_url: str


class RemoteMediaIn(BaseModel):
    name: str = ""
    kind: str
    url: str


class RunIn(BaseModel):
    name: str = ""
    model_id: int
    media_id: int
    input_mode: str = "auto"
    prompt: str = "请描述这个媒体的内容。"
    expected_keywords: list[str] = []
    stream: bool = False
    max_tokens: int = 512
    temperature: float | None = None
    timeout: float = 90.0
    extra_params: dict | None = None
    allow_large_inline: bool = False


def _public_media(row: dict) -> dict:
    item = dict(row)
    item["inline_size_bytes"] = media_service.inline_media_size(row)
    item["inline_request_bytes"] = media_service.estimate_inline_request_bytes(
        row, prompt="", max_tokens=512,
    )
    item["preview_url"] = (
        f"/api/mm/media/{row['id']}/content"
        if row.get("source_type") == "local"
        else row.get("remote_url") or ""
    )
    return item


def _model_ctx(model_id: int) -> tuple[dict, dict]:
    model_row = catalog.get_model(model_id)
    if not model_row:
        raise HTTPException(404, "模型不存在")
    conn = catalog.get_connection(model_row["connection_id"]) if model_row.get("connection_id") else None
    if not conn:
        raise HTTPException(400, "该模型没有绑定连接，请先在资产库补齐")
    return conn, model_row


@router.get("/media")
def list_media() -> list[dict]:
    return [_public_media(row) for row in store.list_media()]


@router.post("/media/local")
def create_local_media(payload: LocalMediaIn) -> dict:
    try:
        row = media_service.save_local(
            name=payload.name, kind=payload.kind, data_url=payload.data_url,
        )
    except media_service.MediaError as exc:
        raise HTTPException(400, str(exc)) from exc
    return _public_media(row)


@router.post("/media/url")
def create_remote_media(payload: RemoteMediaIn) -> dict:
    try:
        row = media_service.register_remote(
            name=payload.name, kind=payload.kind, url=payload.url,
        )
    except media_service.MediaError as exc:
        raise HTTPException(400, str(exc)) from exc
    return _public_media(row)


@router.get("/media/{media_id}/content")
def media_content(media_id: int):
    row = store.get_media(media_id)
    if not row:
        raise HTTPException(404, "媒体不存在")
    if row.get("source_type") == "remote" and row.get("remote_url"):
        return RedirectResponse(row["remote_url"])
    path = row.get("local_path") or ""
    if not path:
        raise HTTPException(404, "本地文件不存在")
    return FileResponse(path, media_type=row.get("mime_type") or "application/octet-stream")


@router.delete("/media/{media_id}")
def delete_media(media_id: int) -> dict:
    if not store.get_media(media_id):
        raise HTTPException(404, "媒体不存在")
    store.delete_media(media_id)
    return {"ok": True}


@router.get("/runs")
def list_runs(limit: int = 50) -> list[dict]:
    return store.list_runs(limit)


@router.post("/runs")
async def create_run(payload: RunIn) -> dict:
    if payload.max_tokens < 1:
        raise HTTPException(400, "max_tokens 必须大于 0")
    conn, model_row = _model_ctx(payload.model_id)
    media = store.get_media(payload.media_id)
    if not media:
        raise HTTPException(404, "媒体不存在")
    try:
        model_extra = json.loads(model_row.get("extra_params") or "{}")
        extra_params = {**model_extra, **(payload.extra_params or {})}
        estimated_request, size_warning = media_service.check_inline_request_size(
            media,
            input_mode=payload.input_mode,
            prompt=payload.prompt,
            max_tokens=payload.max_tokens,
            extra_params=extra_params,
            allow_large=payload.allow_large_inline,
        )
        config = {
            "stream": payload.stream,
            "max_tokens": payload.max_tokens,
            "temperature": payload.temperature,
            "timeout": payload.timeout,
            "input_mode": payload.input_mode,
            "extra_params": extra_params,
            "allow_large_inline": payload.allow_large_inline,
            "estimated_request_bytes": estimated_request or 0,
            "size_warning": size_warning,
        }
        run_id = runner.start_run(
            model_id=model_row["id"],
            model_label=model_row["label"],
            model=model_row["model"],
            conn=conn,
            media=media,
            input_mode=payload.input_mode,
            prompt=payload.prompt,
            expected_keywords=[k.strip() for k in payload.expected_keywords if k.strip()],
            config=config,
            name=payload.name,
        )
    except media_service.MediaError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"run_id": run_id}


@router.get("/runs/{run_id}")
def get_run(run_id: int) -> dict:
    row = store.get_run(run_id)
    if not row:
        raise HTTPException(404, "运行不存在")
    row["progress"] = runner.progress_for(run_id)
    return row


@router.post("/runs/{run_id}/cancel")
def cancel_run(run_id: int) -> dict:
    if not store.get_run(run_id):
        raise HTTPException(404, "运行不存在")
    return {"ok": runner.cancel(run_id)}
