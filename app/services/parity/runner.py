"""基线建立与候选运行的调度（独立于压测任务系统）。"""
import asyncio
from datetime import datetime

from . import store
from .cases import CASE_SET, DIMENSIONS, cases_for
from .executor import execute_case
from .judge import DEFAULT_CONFIG, build_report

_tasks: dict[tuple[str, int], asyncio.Task] = {}
_progress: dict[str, dict] = {}      # key: f"baseline:{id}" / f"run:{id}"


def _task_key(kind: str, ident: int) -> tuple[str, int]:
    return kind, ident


def progress_for(kind: str, ident: int) -> dict:
    return _progress.get(f"{kind}:{ident}", {})


def _set_progress(kind: str, ident: int, **kw) -> None:
    key = f"{kind}:{ident}"
    _progress.setdefault(key, {})
    _progress[key].update(kw)


def build_config(overrides: dict | None = None) -> dict:
    cfg = {**DEFAULT_CONFIG, **(overrides or {})}
    cfg["weights"] = {**DEFAULT_CONFIG["weights"], **(overrides or {}).get("weights", {})}
    cfg["thresholds"] = {**DEFAULT_CONFIG["thresholds"], **(overrides or {}).get("thresholds", {})}
    return cfg


async def _run_baseline(baseline_id: int, conn: dict, model_row: dict, dimensions: list[str]) -> None:
    cases = cases_for(dimensions)
    store.set_baseline_status(baseline_id, "running")
    _set_progress("baseline", baseline_id, total=len(cases), done=0, status="running",
                  started_at=datetime.now().astimezone().isoformat(timespec="seconds"),
                  model=model_row["label"])
    try:
        for i, case in enumerate(cases, 1):
            item = await execute_case(
                base_url=conn["base_url"], endpoint=conn.get("endpoint") or None,
                api_key=conn["_api_key"], model=model_row["model"], case=case,
                extra_params=model_row.get("_extra_params"),
            )
            store.add_baseline_item(baseline_id, item, case.get("payload", {}))
            _set_progress("baseline", baseline_id, done=i, current=case["name"],
                          last_status=item.get("status_code"),
                          last_behavior=item.get("behavior_class"),
                          last_error=item.get("error", ""))
        store.set_baseline_status(baseline_id, "ready")
        _set_progress("baseline", baseline_id, status="ready", done=len(cases))
    except asyncio.CancelledError:
        store.set_baseline_status(baseline_id, "canceled", "用户中止")
        _set_progress("baseline", baseline_id, status="canceled", error="用户中止")
    except Exception as e:      # noqa: BLE001
        store.set_baseline_status(baseline_id, "failed", str(e))
        _set_progress("baseline", baseline_id, status="failed", error=str(e))
    finally:
        _tasks.pop(_task_key("baseline", baseline_id), None)


async def _run_candidate(run_id: int, baseline_id: int, conn: dict, model_row: dict,
                         dimensions: list[str], cfg: dict) -> None:
    baseline = store.get_baseline(baseline_id) or {}
    ref_items = store.get_baseline_items(baseline_id)
    cases = [c for c in cases_for(dimensions) if c["id"] in ref_items]
    store.update_run(run_id, status="running")
    _set_progress("run", run_id, total=len(cases), done=0, status="running",
                  started_at=datetime.now().astimezone().isoformat(timespec="seconds"),
                  model=model_row["label"])
    try:
        for i, case in enumerate(cases, 1):
            item = await execute_case(
                base_url=conn["base_url"], endpoint=conn.get("endpoint") or None,
                api_key=conn["_api_key"], model=model_row["model"], case=case,
                extra_params=model_row.get("_extra_params"),
            )
            store.add_run_item(run_id, item)
            _set_progress("run", run_id, done=i, current=case["name"],
                          last_status=item.get("status_code"),
                          last_behavior=item.get("behavior_class"),
                          last_error=item.get("error", ""))
        cand_items = store.get_run_items(run_id)

        age_days = None
        try:
            created = baseline.get("created_at", "")
            if created:
                age_days = (datetime.now().astimezone()
                            - datetime.fromisoformat(created)).total_seconds() / 86400
        except Exception:      # noqa: BLE001
            age_days = None

        report = build_report(baseline=baseline, run={
            "model": model_row["model"], "model_label": model_row["label"],
            "dimensions": dimensions}, cases=cases, ref_items=ref_items,
            cand_items=cand_items, cfg=cfg, baseline_age_days=age_days)
        store.update_run(run_id, status="finished", verdict=report["verdict"],
                         score=report.get("score"),
                         report_json=__import__("json").dumps(report, ensure_ascii=False),
                         finished_at=datetime.now().astimezone().isoformat(timespec="seconds"))
        _set_progress("run", run_id, status="finished", verdict=report["verdict"])
    except asyncio.CancelledError:
        store.update_run(run_id, status="canceled", error="用户中止")
        _set_progress("run", run_id, status="canceled", error="用户中止")
    except Exception as e:      # noqa: BLE001
        store.update_run(run_id, status="failed", error=str(e))
        _set_progress("run", run_id, status="failed", error=str(e))
    finally:
        _tasks.pop(_task_key("run", run_id), None)


def start_baseline(*, name: str, conn: dict, model_row: dict, dimensions: list[str],
                   config: dict | None = None, notes: str = "") -> int:
    cfg = build_config(config)
    baseline_id = store.create_baseline(
        name=name or f"{model_row['label']}@{datetime.now():%Y-%m-%d}",
        model_label=model_row["label"], model=model_row["model"],
        connection_id=conn["id"], case_set=CASE_SET, dimensions=dimensions,
        config=cfg, notes=notes)
    key = _task_key("baseline", baseline_id)
    _tasks[key] = asyncio.create_task(_run_baseline(baseline_id, conn, model_row, dimensions))
    return baseline_id


def start_candidate(*, baseline_id: int, name: str, conn: dict, model_row: dict,
                    dimensions: list[str], config: dict | None = None) -> int:
    cfg = build_config(config)
    run_id = store.create_run(
        baseline_id=baseline_id, name=name or f"{model_row['label']} vs baseline#{baseline_id}",
        model_label=model_row["label"], model=model_row["model"], connection_id=conn["id"],
        dimensions=dimensions, config=cfg)
    key = _task_key("run", run_id)
    _tasks[key] = asyncio.create_task(
        _run_candidate(run_id, baseline_id, conn, model_row, dimensions, cfg))
    return run_id


def cancel(kind: str, ident: int) -> bool:
    """取消在内存中的一致性任务；未找到表示任务已结束或服务已重启。"""
    task = _tasks.get(_task_key(kind, ident))
    if task is None or task.done():
        return False
    task.cancel()
    return True


def recover_stale_tasks() -> None:
    """服务重启后，数据库里的 running 任务不会继续执行，统一标记为失败。"""
    store.mark_stale_tasks()


def dimension_catalog() -> list[dict]:
    return [{"id": k, "name": v["name"], "weight": v["weight"],
             "cases": len(v["cases"])} for k, v in DIMENSIONS.items()]
