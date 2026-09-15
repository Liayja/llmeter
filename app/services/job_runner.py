"""压测任务调度：启动、进度广播（SSE）、取消。"""
import asyncio

from ..db import now_iso
from . import catalog, key_store, runner_bridge


class JobRunner:
    def __init__(self) -> None:
        self._tasks: dict[int, asyncio.Task] = {}
        self._engines: dict[int, object] = {}
        self._cancel_flags: dict[int, bool] = {}
        self._subscribers: dict[int, set[asyncio.Queue]] = {}
        self._lock = asyncio.Lock()      # M1：同一时刻只跑一个任务
        self._running_task_id: int | None = None

    # ── 订阅 ────────────────────────────────────────────
    def subscribe(self, task_id: int) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._subscribers.setdefault(task_id, set()).add(q)
        return q

    def unsubscribe(self, task_id: int, q: asyncio.Queue) -> None:
        subs = self._subscribers.get(task_id)
        if subs and q in subs:
            subs.discard(q)

    def _publish(self, task_id: int, event: str, data: dict) -> None:
        for q in list(self._subscribers.get(task_id, ())):
            try:
                q.put_nowait({"event": event, "data": data})
            except asyncio.QueueFull:
                pass

    # ── 状态 ────────────────────────────────────────────
    def is_running(self, task_id: int) -> bool:
        t = self._tasks.get(task_id)
        return bool(t and not t.done())

    def busy(self) -> bool:
        return self._running_task_id is not None

    # ── 生命周期 ────────────────────────────────────────
    def start(self, task_id: int, spec: dict) -> None:
        if self.busy():
            raise RuntimeError("已有压测任务在运行，请先等待或中止")
        task = asyncio.create_task(self._run(task_id, spec))
        self._tasks[task_id] = task
        self._running_task_id = task_id
        self._cancel_flags[task_id] = False

    def cancel(self, task_id: int) -> bool:
        self._cancel_flags[task_id] = True
        engine = self._engines.get(task_id)
        if engine is None:
            return task_id in self._tasks      # 还在跑但当前没有引擎（场景切换间隙）
        engine.request_cancel()
        return True

    async def _run(self, task_id: int, spec: dict) -> None:
        catalog.update_task(task_id, status="running", started_at=now_iso())
        self._publish(task_id, "status", {"status": "running"})

        def on_progress(p: dict) -> None:
            self._publish(task_id, "progress", p)

        def on_engine(engine) -> None:
            self._engines[task_id] = engine

        try:
            if spec.get("mode") == "scenarios":
                result = await runner_bridge.run_scenarios(
                    spec, on_progress=on_progress, on_engine=on_engine,
                    is_cancelled=lambda: self._cancel_flags.get(task_id, False))
                status = "canceled" if result.get("canceled") else "success"
                totals = {k: result.get(k, 0) for k in ("total", "success", "fail")}
            else:
                result = await runner_bridge.run_chat(spec, on_progress=on_progress,
                                                      on_engine=on_engine)
                metrics = result["metrics"]
                status = "canceled" if metrics.get("canceled") else "success"
                totals = {k: metrics.get(k, 0) for k in ("total", "success", "fail")}
            catalog.update_task(
                task_id, status=status, total=totals["total"], success=totals["success"],
                fail=totals["fail"], result_dir=result.get("result_dir"),
                summary_path=result.get("summary_path"), excel_path=result.get("excel_path"),
                finished_at=now_iso(),
            )
            self._publish(task_id, "status", {
                "status": status, **totals, "excel_path": result.get("excel_path"),
            })
        except Exception as e:      # noqa: BLE001
            catalog.update_task(task_id, status="failed", error=str(e), finished_at=now_iso())
            self._publish(task_id, "error", {"message": str(e)})
            self._publish(task_id, "status", {"status": "failed", "error": str(e)})
        finally:
            self._engines.pop(task_id, None)
            self._cancel_flags.pop(task_id, None)
            self._tasks.pop(task_id, None)
            self._running_task_id = None
            # 关闭订阅者队列，让 SSE 自然结束
            for q in list(self._subscribers.get(task_id, ())):
                try:
                    q.put_nowait({"event": "close", "data": {}})
                except asyncio.QueueFull:
                    pass


runner = JobRunner()


def resolve_connection_secret(connection: dict) -> str:
    return key_store.resolve(connection)
