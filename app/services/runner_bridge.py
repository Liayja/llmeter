"""Web 层与现有压测引擎/报告模块的桥接（不复制指标口径）。"""
import json
import sys
from datetime import datetime
from pathlib import Path

from ..settings import PROJECT_ROOT

_SRC = PROJECT_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from engine import (LLMBench, build_chat_payload, build_chat_url,   # noqa: E402
                    count_tokens, normalize_extra_params)
from output import (save_results, save_excel, save_comparison,   # noqa: E402
                    _row_from_result)


def resolve_prompt(spec: dict) -> str:
    """support prompt 文本或 prompt_file（相对项目根）。"""
    prompt_file = spec.get("prompt_file")
    if prompt_file:
        fp = Path(prompt_file)
        if not fp.is_absolute():
            fp = PROJECT_ROOT / fp
        return fp.read_text(encoding="utf-8").strip()
    return spec.get("prompt") or "Hello, world!"


def build_engine_config(spec: dict) -> dict:
    """任务快照 → LLMBench 参数，字段与 bench.build_engine_config 对齐。"""
    return {
        "base_url": (spec.get("base_url") or "").rstrip("/"),
        "endpoint": spec.get("endpoint") or None,
        "api_key": spec.get("api_key") or "",
        "model": spec["model"],
        "prompt": resolve_prompt(spec),
        "concurrency": int(spec.get("concurrency", 10)),
        "total_requests": int(spec.get("requests", 100)),
        "timeout": int(spec.get("timeout", 30)),
        "output_dir": spec.get("output_dir") or "bench_results",
        "max_tokens": int(spec.get("max_tokens", 2048)),
        "temperature": spec.get("temperature"),
        "stream": bool(spec.get("stream", True)),
        "retries": int(spec.get("retries", 2)),
        "retry_backoff": float(spec.get("retry_backoff", 1.0)),
        "read_timeout": spec.get("read_timeout"),
        "unique_prefix": bool(spec.get("unique_prefix", False)),
        "verbose": False,
        "extra_params": spec.get("extra_params") or None,
        "http2": bool(spec.get("http2", False)),
        "warmup": int(spec.get("warmup", 2)),
        "track_cache": bool(spec.get("track_cache", True)),
        "qps": spec.get("qps"),
        "duration": spec.get("duration"),
    }


def _snapshot(output_dir: str, pattern: str) -> set:
    base = Path(output_dir)
    if not base.is_absolute():
        base = PROJECT_ROOT / base
    if not base.exists():
        return set()
    return set(base.rglob(pattern))


async def run_chat(spec: dict, on_progress=None, on_engine=None) -> dict:
    """执行一次单模型压测并落盘 JSON/Excel；返回产物路径与 metrics。"""
    cfg = build_engine_config(spec)
    output_dir = cfg["output_dir"]

    before_json = _snapshot(output_dir, "*.json")
    before_xlsx = _snapshot(output_dir, "*.xlsx")

    engine = LLMBench(**cfg, on_progress=on_progress)
    if on_engine is not None:
        on_engine(engine)
    metrics = await engine.run()

    task_name = spec.get("name") or cfg["model"]
    save_results(metrics, engine.results,
                 {**cfg, "total_requests": cfg["total_requests"], "chat_url": engine.chat_url},
                 "chat")
    new_json = sorted(_snapshot(output_dir, "*.json") - before_json)
    summary_path = str(new_json[-1]) if new_json else None
    result_dir = str(Path(summary_path).parent) if summary_path else None

    excel_path = None
    try:
        row = _row_from_result(task_name, metrics, cfg["concurrency"])
        row["_model"] = cfg["model"]
        row["model_label"] = cfg["model"]
        save_excel([row], {"单次": [row]}, cfg["model"], output_dir,
                   show_model=False, charts=False)
        new_xlsx = sorted(_snapshot(output_dir, "*.xlsx") - before_xlsx)
        excel_path = str(new_xlsx[-1]) if new_xlsx else None
    except Exception as e:   # Excel 失败不影响主流程
        print(f"[warn] 生成 Excel 失败: {e}")

    return {
        "metrics": metrics,
        "summary_path": summary_path,
        "excel_path": excel_path,
        "result_dir": result_dir,
    }


def _group_of(name: str) -> str:
    """场景分组：与 CLI report_scenarios 的规则一致（取 '-' 前的部分）。"""
    dash = name.find("-")
    return name[:dash] if dash > 0 else "其他"


async def run_scenarios(spec: dict, on_progress=None, on_engine=None,
                        is_cancelled=None) -> dict:
    """多模型 × 多场景：逐个跑，最后生成对比报告（MD/CSV/Excel）。

    复用 LLMBench 与 output 的对比报告函数，指标口径与 CLI 完全一致。
    """
    models = spec.get("models") or []
    scenarios = spec.get("scenarios") or []
    defaults = dict(spec.get("defaults") or {})
    output_dir = spec.get("output_dir") or defaults.get("output_dir") or "bench_results"
    levels = sorted({
        int(x) for x in (defaults.get("concurrency_levels")
                         or [defaults.get("concurrency", 10)])
        if int(x) > 0
    }) or [int(defaults.get("concurrency", 10))]
    total = len(models) * len(scenarios) * len(levels)
    rows: list[dict] = []
    idx = 0
    canceled = False

    def _cancelled() -> bool:
        return bool(is_cancelled and is_cancelled())

    for model in models:
        for sc in scenarios:
            scene_name = sc.get("name") or "未命名场景"
            for level in levels:
                if _cancelled():
                    canceled = True
                    break
                idx += 1
                merged = {**defaults}
                merged.update(sc.get("prompt") and {"prompt": sc["prompt"]} or {})
                merged.update(sc.get("prompt_file") and {"prompt_file": sc["prompt_file"]} or {})
                merged.update(sc.get("overrides") or {})
                # 阶梯矩阵的并发点优先级最高，避免场景 overrides 覆盖矩阵设置。
                merged["concurrency"] = level
                target_prompt = resolve_prompt(merged)
                target_input_tokens = count_tokens(target_prompt) if target_prompt else 0
                cfg = build_engine_config({
                    "model": model["model"],
                    "base_url": model["base_url"],
                    "endpoint": model.get("endpoint"),      # 关键：厂商自定义路径必须透传
                    "api_key": model.get("api_key") or "",
                    "extra_params": model.get("extra_params"),
                    "output_dir": output_dir,
                    "prompt": merged.get("prompt"),
                    "prompt_file": merged.get("prompt_file"),
                    **{k: v for k, v in merged.items() if k not in ("prompt", "prompt_file")},
                })
                scene_display = f"{scene_name} · {level}并发"

                def _progress(p: dict, _idx=idx, _scene=scene_display,
                              _level=level) -> None:
                    if on_progress is None:
                        return
                    on_progress({
                        **p,
                        "scene": _scene,
                        "scene_name": scene_name,
                        "scene_index": _idx,
                        "scenes_total": total,
                        "concurrency": _level,
                    })

                engine = LLMBench(**cfg, on_progress=_progress)
                if on_engine is not None:
                    on_engine(engine)
                metrics = await engine.run()
                if metrics.get("canceled"):
                    canceled = True

                row = _row_from_result(scene_name, metrics, cfg["concurrency"])
                row["_model"] = cfg["model"]
                row["model_label"] = model.get("label") or cfg["model"]
                row["target_input_tokens"] = target_input_tokens
                actual_input = row.get("avg_input_tokens")
                if actual_input is not None and target_input_tokens:
                    row["input_token_deviation"] = actual_input - target_input_tokens
                    row["input_token_deviation_pct"] = (
                        (actual_input - target_input_tokens) / target_input_tokens * 100
                    )
                else:
                    row["input_token_deviation"] = None
                    row["input_token_deviation_pct"] = None
                row["is_input_ladder"] = True
                rows.append(row)
                # 每个场景/并发点仍单独落一份 JSON，便于回溯
                before_json = _snapshot(output_dir, "*.json")
                save_results(metrics, engine.results,
                             {**cfg, "chat_url": engine.chat_url}, "chat")
                new_json = sorted(_snapshot(output_dir, "*.json") - before_json)
                if new_json:
                    # 同一秒内多个场景的文件名会撞车：补上场景名和并发点区分
                    src = new_json[-1]
                    safe_scene = "".join(c for c in scene_name if c not in '\\/:*?"<>|')
                    try:
                        src.rename(src.with_name(
                            f"{src.stem}_{safe_scene}_c{level}{src.suffix}"
                        ))
                    except OSError:
                        pass
                if canceled:
                    break
            if canceled:
                break
        if canceled:
            break

    result_dir = None
    summary_path = None
    excel_path = None
    if rows:
        date_dir = Path(output_dir)
        if not date_dir.is_absolute():
            date_dir = PROJECT_ROOT / date_dir
        date_dir = date_dir / datetime.now().strftime("%Y-%m-%d")
        date_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%H%M%S")
        title = models[0].get("label") if len(models) == 1 else "跨模型"

        groups: dict[str, list] = {}
        for r in rows:
            groups.setdefault(_group_of(r["name"]), []).append(r)
        save_comparison(rows, title, output_dir, show_model=len(models) > 1)

        before_xlsx = _snapshot(output_dir, "*.xlsx")
        save_excel(rows, groups, title, output_dir,
                   show_model=len(models) > 1, charts=False)
        new_xlsx = sorted(_snapshot(output_dir, "*.xlsx") - before_xlsx)
        excel_path = str(new_xlsx[-1]) if new_xlsx else None

        comparison_json = date_dir / f"comparison_{title}_{stamp}.json"
        comparison_json.write_text(json.dumps(
            {"models": [m.get("label") for m in models],
             "scenarios": [s.get("name") for s in scenarios],
             "concurrency_levels": levels,
             "rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
        summary_path = str(comparison_json)
        result_dir = str(date_dir)

    return {
        "mode": "scenarios",
        "rows": rows,
        "summary_path": summary_path,
        "excel_path": excel_path,
        "result_dir": result_dir,
        "canceled": canceled,
        "total": len(rows),
        "success": sum(1 for r in rows if r.get("fail", 0) == 0),
        "fail": sum(1 for r in rows if r.get("fail", 0) > 0),
    }
