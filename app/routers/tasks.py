"""任务 API：创建 / 启动 / 取消 / 进度(SSE)。"""
import asyncio
import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..services import catalog, job_runner, key_store

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


class TaskIn(BaseModel):
    name: str = ""
    mode: str = "chat"                 # chat | scenarios
    model_id: int | None = None
    model_ids: list[int] = []          # scenarios 模式：多个模型
    scenario_ids: list[int] = []       # scenarios 模式：多个场景
    # 单次压测参数（缺省值与 CLI 保持一致）
    concurrency: int = 10
    concurrency_levels: list[int] = []  # 阶梯矩阵：场景模式下的多个并发点
    requests: int = 100
    timeout: int = 30
    max_tokens: int = 2048
    temperature: float | None = None
    stream: bool = True
    warmup: int = 2
    retries: int = 2
    track_cache: bool = True
    unique_prefix: bool = False
    http2: bool = False
    read_timeout: float | None = None
    qps: float | None = None
    duration: int | None = None
    prompt: str = "Hello, world!"
    prompt_file: str | None = None
    output_dir: str = "bench_results"


def _build_spec(payload: TaskIn) -> dict:
    """组装运行时 spec：此处才解析密钥，快照里不落明文。"""
    model_row = catalog.get_model(payload.model_id)
    if not model_row:
        raise HTTPException(404, "模型不存在")
    conn = catalog.get_connection(model_row["connection_id"]) if model_row.get("connection_id") else None
    if not conn:
        raise HTTPException(400, "该模型没有绑定连接，请先在资产库补齐")
    extra = json.loads(model_row.get("extra_params") or "{}")
    return {
        "name": payload.name or f"{model_row['label']} 压测",
        "model": model_row["model"],
        "model_label": model_row["label"],
        "connection_id": conn["id"],
        "base_url": conn["base_url"],
        "endpoint": conn.get("endpoint") or None,
        "api_key": key_store.resolve(conn),
        "extra_params": extra or None,
        "prompt": payload.prompt,
        "prompt_file": payload.prompt_file,
        "concurrency": payload.concurrency,
        "requests": payload.requests,
        "timeout": payload.timeout,
        "max_tokens": payload.max_tokens,
        "temperature": payload.temperature,
        "stream": payload.stream,
        "warmup": payload.warmup,
        "retries": payload.retries,
        "track_cache": payload.track_cache,
        "unique_prefix": payload.unique_prefix,
        "http2": payload.http2,
        "read_timeout": payload.read_timeout,
        "qps": payload.qps,
        "duration": payload.duration,
        "output_dir": payload.output_dir,
    }


@router.post("")
def create_task(payload: TaskIn) -> dict:
    if payload.mode == "scenarios":
        models = [catalog.get_model(i) for i in payload.model_ids]
        scenarios = [catalog.get_scenario(i) for i in payload.scenario_ids]
        if not all(models) or not all(scenarios):
            raise HTTPException(400, "模型或场景不存在，请检查选择")
        defaults = {
            "concurrency": payload.concurrency,
            "concurrency_levels": payload.concurrency_levels or [payload.concurrency],
            "requests": payload.requests,
            "timeout": payload.timeout, "max_tokens": payload.max_tokens,
            "temperature": payload.temperature, "stream": payload.stream,
            "warmup": payload.warmup, "retries": payload.retries,
            "track_cache": payload.track_cache, "unique_prefix": payload.unique_prefix,
            "http2": payload.http2, "read_timeout": payload.read_timeout,
            "qps": payload.qps, "duration": payload.duration,
        }
        levels = sorted({int(x) for x in defaults["concurrency_levels"] if int(x) > 0})
        if not levels:
            raise HTTPException(400, "至少配置一个并发点")
        defaults["concurrency_levels"] = levels
        name = payload.name or f"{len(models)} 模型 × {len(scenarios)} 场景 × {len(levels)} 并发点"
        snapshot = {
            "mode": "scenarios",
            "models": [{
                "label": m["label"], "model": m["model"],
                "connection_id": m["connection_id"],
                "extra_params": json.loads(m.get("extra_params") or "{}"),
            } for m in models],
            "scenarios": [{
                "name": s["name"], "prompt": s["prompt"], "prompt_file": s["prompt_file"],
                "overrides": json.loads(s.get("overrides") or "{}"),
            } for s in scenarios],
            "defaults": defaults,
            "output_dir": payload.output_dir,
        }
        task_id = catalog.create_task(name, "scenarios", snapshot)
        return {"task_id": task_id, "name": name}

    if not payload.model_id:
        raise HTTPException(400, "请选择模型")
    spec = _build_spec(payload)
    snapshot = {k: v for k, v in spec.items() if k != "api_key"}   # 快照不含明文密钥
    task_id = catalog.create_task(spec["name"], "chat", snapshot)
    return {"task_id": task_id, "name": spec["name"]}


@router.post("/{task_id}/start")
async def start_task(task_id: int) -> dict:
    task = catalog.get_task(task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    if task["status"] == "running" or job_runner.runner.is_running(task_id):
        raise HTTPException(409, "任务已在运行")
    snapshot = json.loads(task["config_snapshot"])
    if task["mode"] == "scenarios":
        models = []
        for m in snapshot["models"]:
            conn = catalog.get_connection(m["connection_id"])
            if not conn:
                raise HTTPException(400, f"模型 {m['label']} 的连接已不存在")
            models.append({**m, "base_url": conn["base_url"],
                           "endpoint": conn.get("endpoint") or None,
                           "api_key": key_store.resolve(conn)})
        spec = {**snapshot, "models": models}
    else:
        conn = catalog.get_connection(snapshot["connection_id"])
        if not conn:
            raise HTTPException(400, "连接已不存在，无法启动")
        spec = {**snapshot, "api_key": key_store.resolve(conn)}
    try:
        job_runner.runner.start(task_id, spec)
    except RuntimeError as e:
        raise HTTPException(409, str(e)) from e
    return {"ok": True, "task_id": task_id}


@router.post("/{task_id}/cancel")
async def cancel_task(task_id: int) -> dict:
    ok = job_runner.runner.cancel(task_id)
    return {"ok": ok}


@router.get("")
def list_tasks(limit: int = 50) -> list[dict]:
    return catalog.list_tasks(limit)


@router.get("/{task_id}")
def get_task(task_id: int) -> dict:
    task = catalog.get_task(task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    return task


@router.get("/{task_id}/events")
async def task_events(task_id: int):
    """SSE：推送 progress / status / error 事件。"""
    task = catalog.get_task(task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    q = job_runner.runner.subscribe(task_id)

    async def gen():
        try:
            yield f"event: status\ndata: {json.dumps({'status': task['status']}, ensure_ascii=False)}\n\n"
            if task["status"] in ("success", "failed", "canceled"):
                return
            while True:
                try:
                    item = await asyncio.wait_for(q.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
                    continue
                if item["event"] == "close":
                    break
                yield (f"event: {item['event']}\n"
                       f"data: {json.dumps(item['data'], ensure_ascii=False)}\n\n")
        finally:
            job_runner.runner.unsubscribe(task_id, q)

    return StreamingResponse(gen(), media_type="text/event-stream")
