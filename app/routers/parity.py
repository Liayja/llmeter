"""基准一致性测试 API（独立于压测）。"""
import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..services import catalog, key_store
from ..services.parity import runner, store
from ..services.parity.cases import CASES

router = APIRouter(prefix="/api/parity", tags=["parity"])

# 用例索引：把 case_id 映射到名称/维度，供基线明细展示
_CASE_INDEX = {c["id"]: c for c in CASES}


class BaselineIn(BaseModel):
    name: str = ""
    model_id: int
    dimensions: list[str] = ["D1", "D2", "D8", "D9"]
    config: dict | None = None
    notes: str = ""


class RunIn(BaseModel):
    baseline_id: int
    name: str = ""
    model_id: int
    dimensions: list[str] | None = None      # 不填则沿用基线的维度
    config: dict | None = None


def _model_ctx(model_id: int) -> tuple[dict, dict]:
    """返回 (连接上下文, 模型上下文)，其中含运行时密钥与 extra_params（不下发到前端）。"""
    model_row = catalog.get_model(model_id)
    if not model_row:
        raise HTTPException(404, "模型不存在")
    conn = catalog.get_connection(model_row["connection_id"]) if model_row.get("connection_id") else None
    if not conn:
        raise HTTPException(400, "该模型没有绑定连接")
    extra = json.loads(model_row.get("extra_params") or "{}")
    return ({**conn, "_api_key": key_store.resolve(conn)},
            {**model_row, "_extra_params": extra})


@router.get("/dimensions")
def dimensions() -> list[dict]:
    """可选的测试维度（界面用于渲染勾选卡片）。"""
    return runner.dimension_catalog()


@router.get("/baselines")
def list_baselines() -> list[dict]:
    return store.list_baselines()


@router.post("/baselines")
async def create_baseline(payload: BaselineIn) -> dict:
    dims = [d for d in payload.dimensions if d in runner.DIMENSIONS]
    if not dims:
        raise HTTPException(400, "至少选择一个测试维度")
    conn, model_row = _model_ctx(payload.model_id)
    baseline_id = runner.start_baseline(name=payload.name, conn=conn, model_row=model_row,
                                        dimensions=dims, config=payload.config,
                                        notes=payload.notes)
    return {"baseline_id": baseline_id}


@router.get("/baselines/{baseline_id}")
def get_baseline(baseline_id: int) -> dict:
    b = store.get_baseline(baseline_id)
    if not b:
        raise HTTPException(404, "基线不存在")
    items = store.get_baseline_items(baseline_id)
    detail = []
    for cid, v in items.items():
        case = _CASE_INDEX.get(cid, {})
        try:
            request_payload = json.loads(v.get("payload_json") or "{}")
        except Exception:      # noqa: BLE001
            request_payload = {}
        detail.append({
            "case_id": cid,
            "name": case.get("name", cid),
            "dimension": case.get("dimension", v.get("dimension", "")),
            "request": request_payload,          # 该用例实际发出的请求体
            "raw_response": v.get("raw_response", ""),   # 原始响应体（含 4xx 报错原文）
            "status_code": v.get("status_code"),
            "behavior_class": v.get("behavior_class"),
            "prompt_tokens": v.get("prompt_tokens"),
            "completion_tokens": v.get("completion_tokens"),
            "latency": v.get("latency"),
            "error": (v.get("error") or "")[:200],
            "output": (v.get("output_text") or "")[:200],
            "usage": v.get("usage") or {},
        })
    detail.sort(key=lambda x: (x["dimension"], x["case_id"]))
    b["items_detail"] = detail
    b["progress"] = runner.progress_for("baseline", baseline_id)
    return b


@router.delete("/baselines/{baseline_id}")
def delete_baseline(baseline_id: int) -> dict:
    if not store.get_baseline(baseline_id):
        raise HTTPException(404, "基线不存在")
    store.delete_baseline(baseline_id)
    return {"ok": True}


@router.get("/runs")
def list_runs(limit: int = 50) -> list[dict]:
    return store.list_runs(limit)


@router.post("/runs")
async def create_run(payload: RunIn) -> dict:
    baseline = store.get_baseline(payload.baseline_id)
    if not baseline:
        raise HTTPException(404, "基线不存在")
    dims = payload.dimensions or [d for d in (baseline.get("dimensions") or "").split(",") if d]
    dims = [d for d in dims if d in runner.DIMENSIONS]
    if not dims:
        raise HTTPException(400, "至少选择一个测试维度")
    conn, model_row = _model_ctx(payload.model_id)
    run_id = runner.start_candidate(baseline_id=payload.baseline_id, name=payload.name,
                                    conn=conn, model_row=model_row, dimensions=dims,
                                    config=payload.config)
    return {"run_id": run_id}


@router.get("/runs/{run_id}")
def get_run(run_id: int) -> dict:
    run = store.get_run(run_id)
    if not run:
        raise HTTPException(404, "运行不存在")
    report = {}
    try:
        report = json.loads(run.get("report_json") or "{}")
    except Exception:      # noqa: BLE001
        report = {}
    return {**run, "report": report, "progress": runner.progress_for("run", run_id)}


@router.get("/runs/{run_id}/markdown")
def run_markdown(run_id: int) -> dict:
    """把报告导出为 Markdown 文本（便于归档/发给供应商）。"""
    run = store.get_run(run_id)
    if not run:
        raise HTTPException(404, "运行不存在")
    report = json.loads(run.get("report_json") or "{}")
    if not report:
        raise HTTPException(400, "报告尚未生成")

    lines = [f"# 基准一致性报告：{run['name']}", "",
             f"- 候选模型：{run['model_label']}（{run['model']}）",
             f"- 基线：{report.get('baseline', {}).get('name')}（{report.get('baseline', {}).get('case_set')}）",
             f"- 结论：**{report.get('verdict')}**"
             + (f" · 等价度 {report.get('score')}" if report.get("score") is not None else ""),
             f"- 生成时间：{run.get('finished_at') or run.get('created_at')}", ""]

    gate = report.get("gate", {})
    if not gate.get("ok", True):
        lines += ["## ⚠️ 不可比（闸门未通过）", ""]
        lines += [f"- {r}" for r in gate.get("reasons", [])]
        lines += [f"- 建议：{s}" for s in gate.get("suggestions", [])]
        lines.append("")
    if report.get("red_flags"):
        lines += ["## 🚩 一票否决项", ""] + [f"- {f}" for f in report["red_flags"]] + [""]

    lines += ["## 维度结果", "", "| 维度 | 名称 | 结论 | 指标 |", "|------|------|------|------|"]
    for d in report.get("dimensions", []):
        icon = {"green": "🟢", "yellow": "🟠", "red": "🔴"}.get(d["verdict"], "—")
        metrics = "；".join(f"{k}={v}" for k, v in d.get("metrics", {}).items())
        lines.append(f"| {d['dimension']} | {d['name']} | {icon} | {metrics} |")
    lines.append("")

    lines += ["## 逐条用例", "", "| 用例 | 维度 | 判定 | 说明 |", "|------|------|------|------|"]
    for c in report.get("cases", []):
        icon = {"pass": "✅", "fail": "❌", "skip": "⚠️"}.get(c["verdict"], "—")
        lines.append(f"| {c['name']} | {c['dimension']} | {icon} | {c['reason']} |")
    lines.append("")
    lines += ["## 配置快照", "", "```json",
              json.dumps(report.get("config", {}), ensure_ascii=False, indent=2), "```"]
    return {"markdown": "\n".join(lines)}
