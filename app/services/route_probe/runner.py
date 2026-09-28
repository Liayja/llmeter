"""路由指纹检测调度器。"""
import asyncio
import json
from datetime import datetime

import httpx

from ..key_store import resolve
from . import store
from .analysis import analyze_attempts
from .executor import execute_attempt

_tasks: dict[int, asyncio.Task] = {}
_progress: dict[int, dict] = {}


def progress_for(run_id: int) -> dict:
    return _progress.get(run_id, {})


def _set_progress(run_id: int, **kw) -> None:
    _progress.setdefault(run_id, {}).update(kw)


def _levels(config: dict) -> list[int]:
    values = config.get("concurrency_levels") or [config.get("concurrency", 1)]
    return sorted({int(x) for x in values if int(x) > 0}) or [1]


async def _execute(run_id: int, conn: dict, model_row: dict, prompt: str, config: dict) -> None:
    store.update_run(run_id, status="running")
    levels = _levels(config)
    requests = int(config.get("requests", 100))
    total = requests * len(levels)
    _set_progress(run_id, status="running", total=total, done=0,
                  current=f"并发 {levels[0]}", verdict="")
    try:
        all_attempts: list[dict] = []
        sequence = 0
        for concurrency in levels:
            limits = httpx.Limits(
                max_connections=max(concurrency * 2, 4),
                max_keepalive_connections=max(concurrency * 2, 4),
            )
            async with httpx.AsyncClient(
                http2=bool(config.get("http2", False)), limits=limits,
            ) as client:
                lock = asyncio.Lock()
                next_index = 0

                async def one_worker() -> None:
                    nonlocal next_index, sequence
                    while True:
                        async with lock:
                            if next_index >= requests:
                                return
                            local_index = next_index
                            next_index += 1
                            sequence += 1
                            current_sequence = sequence
                        item = await execute_attempt(
                            client=client,
                            base_url=conn["base_url"],
                            endpoint=conn.get("endpoint") or None,
                            api_key=resolve(conn),
                            model=model_row["model"],
                            prompt=prompt,
                            stream=bool(config.get("stream", False)),
                            max_tokens=int(config.get("max_tokens", 16)),
                            temperature=config.get("temperature", 0),
                            timeout=float(config.get("timeout", 120)),
                            extra_params=model_row.get("_extra_params"),
                            concurrency=concurrency,
                            sequence=current_sequence,
                            unique_prefix=bool(config.get("unique_prefix", True)),
                            logprobs=bool(config.get("logprobs", False)),
                        )
                        store.add_attempt(run_id, item)
                        all_attempts.append(item)
                        _set_progress(
                            run_id, done=len(all_attempts), current=f"并发 {concurrency}",
                            last_status=item.get("status_code"),
                            last_error=item.get("error", ""),
                        )

                workers = [asyncio.create_task(one_worker()) for _ in range(concurrency)]
                await asyncio.gather(*workers)
        summary = analyze_attempts(all_attempts, config)
        store.update_run(
            run_id, status="finished", verdict=summary["verdict"],
            confidence=summary["confidence"],
            summary_json=json.dumps(summary, ensure_ascii=False),
            finished_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        )
        _set_progress(run_id, status="finished", done=total, current="完成",
                      verdict=summary["verdict"], confidence=summary["confidence"])
    except asyncio.CancelledError:
        store.update_run(run_id, status="canceled", error="用户中止",
                         finished_at=datetime.now().astimezone().isoformat(timespec="seconds"))
        _set_progress(run_id, status="canceled", error="用户中止")
    except Exception as exc:      # noqa: BLE001
        store.update_run(run_id, status="failed", error=str(exc),
                         finished_at=datetime.now().astimezone().isoformat(timespec="seconds"))
        _set_progress(run_id, status="failed", error=str(exc))
    finally:
        _tasks.pop(run_id, None)


def start_run(*, name: str, model_id: int | None, model_label: str, model: str,
              conn: dict, prompt: str, config: dict, preset: str = "quick") -> int:
    run_id = store.create_run(
        name=name or f"{model_label} 路由指纹检测",
        model_id=model_id, model_label=model_label, model=model,
        connection_id=conn.get("id"), preset=preset, config=config,
    )
    _tasks[run_id] = asyncio.create_task(
        _execute(
            run_id=run_id, conn=conn,
            model_row={"model": model, "_extra_params": config.get("extra_params") or {}},
            prompt=prompt, config=config,
        )
    )
    return run_id


def cancel(run_id: int) -> bool:
    task = _tasks.get(run_id)
    if task is None or task.done():
        return False
    task.cancel()
    return True


def recover_stale_runs() -> None:
    store.mark_stale_runs()


async def shutdown() -> None:
    tasks = [task for task in _tasks.values() if not task.done()]
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    _tasks.clear()
