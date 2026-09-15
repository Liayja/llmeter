"""结果浏览与产下载。"""
import json
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from ..services import catalog

router = APIRouter(prefix="/api/results", tags=["results"])


@router.get("")
def list_results(limit: int = 50) -> list[dict]:
    rows = catalog.list_tasks(limit)
    return [r for r in rows if r["status"] in ("success", "canceled", "failed")]


@router.get("/{task_id}")
def result_detail(task_id: int) -> dict:
    task = catalog.get_task(task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    summary = None
    rows = None
    if task.get("summary_path") and Path(task["summary_path"]).exists():
        try:
            data = json.loads(Path(task["summary_path"]).read_text(encoding="utf-8"))
            summary = data.get("summary")
            rows = data.get("rows")          # scenarios 模式：对比表原始行
        except Exception:       # noqa: BLE001
            summary = None
    files = []
    for key, kind in (("summary_path", "json"), ("excel_path", "xlsx")):
        p = task.get(key)
        if p and Path(p).exists():
            files.append({"kind": kind, "name": Path(p).name})
    return {"task": task, "summary": summary, "rows": rows, "files": files}


@router.get("/{task_id}/download")
def download(task_id: int, kind: str = "json") -> FileResponse:
    task = catalog.get_task(task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    field = "summary_path" if kind == "json" else "excel_path"
    path = task.get(field)
    if not path or not Path(path).exists():
        raise HTTPException(404, "产物不存在")
    return FileResponse(path, filename=Path(path).name)
