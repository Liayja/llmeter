"""路由指纹检测 API。"""
import hashlib
import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ..services import catalog
from ..services.runner_bridge import count_tokens
from ..services.route_probe import runner, store
from ..settings import (
    PROJECT_ROOT,
    ROUTE_PROBE_COST_MAX_TOKENS,
    ROUTE_PROBE_COST_WARN_TOKENS,
)

router = APIRouter(prefix="/api/route-probe", tags=["route-probe"])

BASELINE_PROMPT = (
    '请只输出以下 JSON，不要解释：\n'
    '{"probe":"Q7K2M9","ok":true}'
)
TOKENIZER_PROMPT = (
    "请将下面内容压缩成一行，不要解释：\n"
    "中文测试 + English test + JSON {\"a\":1,\"b\":true} + "
    "code print(\"x\") + 🚀 + 数字 1234567890。"
)


class RunIn(BaseModel):
    name: str = ""
    model_id: int
    preset: str = "quick"
    prompt: str = BASELINE_PROMPT
    prompt_file: str = ""
    requests: int = 30
    concurrency_levels: list[int] = [1]
    stream: bool = False
    max_tokens: int = 4
    temperature: float = 0
    timeout: float = 120
    unique_prefix: bool = True
    http2: bool = False
    logprobs: bool = False
    extra_params: dict | None = None
    allow_large_cost: bool = False


def _model_ctx(model_id: int) -> tuple[dict, dict]:
    model_row = catalog.get_model(model_id)
    if not model_row:
        raise HTTPException(404, "模型不存在")
    conn = catalog.get_connection(model_row["connection_id"]) if model_row.get("connection_id") else None
    if not conn:
        raise HTTPException(400, "该模型没有绑定连接")
    return conn, model_row


def _resolve_prompt(payload: RunIn) -> str:
    if not payload.prompt_file:
        return payload.prompt
    path = Path(payload.prompt_file)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    if not path.exists():
        raise HTTPException(400, f"Prompt 文件不存在：{payload.prompt_file}")
    return path.read_text(encoding="utf-8")


@router.get("/presets")
def presets() -> list[dict]:
    return [
        {
            "id": "quick", "name": "连接路由快速检测",
            "description": "30 次短请求，单并发，默认唯一前缀，优先判断连接和入口指纹。",
            "requests": 30, "concurrency_levels": [1],
            "stream": False, "max_tokens": 4, "unique_prefix": True,
            "prompt": BASELINE_PROMPT,
        },
        {
            "id": "token", "name": "Tokenizer 指纹",
            "description": "相同 Prompt 不加唯一前缀，重点观察 prompt_tokens 是否出现多组。",
            "requests": 30, "concurrency_levels": [1],
            "stream": False, "max_tokens": 4, "unique_prefix": False,
            "prompt": TOKENIZER_PROMPT,
        },
        {
            "id": "standard", "name": "标准确认",
            "description": "100 次短请求，适合确认是否存在稳定的多组指纹。",
            "requests": 100, "concurrency_levels": [1],
            "stream": False, "max_tokens": 8, "unique_prefix": True,
            "prompt": BASELINE_PROMPT,
        },
        {
            "id": "failure", "name": "长输入/流式复现",
            "description": "少量长输入和流式请求，用于复现超时、流式中断和渠道差异；成本较高。",
            "requests": 30, "concurrency_levels": [1],
            "stream": True, "max_tokens": 64, "unique_prefix": True,
            "prompt": "",
        },
    ]


@router.get("/runs")
def list_runs(limit: int = 50) -> list[dict]:
    return store.list_runs(limit)


@router.post("/runs")
async def create_run(payload: RunIn) -> dict:
    if payload.requests < 1 or payload.requests > 10000:
        raise HTTPException(400, "requests 必须在 1 到 10000 之间")
    levels = sorted({int(x) for x in payload.concurrency_levels if int(x) > 0})
    if not levels or max(levels) > 100:
        raise HTTPException(400, "concurrency_levels 必须包含 1 到 100 之间的并发数")
    conn, model_row = _model_ctx(payload.model_id)
    model_extra = json.loads(model_row.get("extra_params") or "{}")
    prompt = _resolve_prompt(payload)
    if not prompt.strip():
        raise HTTPException(400, "Prompt 不能为空；长输入复现请选择 Prompt 文件")
    prompt_tokens_est = count_tokens(prompt)
    estimated_tokens = payload.requests * len(levels) * (prompt_tokens_est + payload.max_tokens)
    if estimated_tokens > ROUTE_PROBE_COST_MAX_TOKENS and not payload.allow_large_cost:
        raise HTTPException(
            400,
            f"预计消耗约 {estimated_tokens:,} Token，超过 "
            f"{ROUTE_PROBE_COST_MAX_TOKENS:,} 的安全上限；如确认要运行，请开启 allow_large_cost。",
        )
    if estimated_tokens > ROUTE_PROBE_COST_WARN_TOKENS and not payload.allow_large_cost:
        raise HTTPException(
            400,
            f"预计消耗约 {estimated_tokens:,} Token，超过 "
            f"{ROUTE_PROBE_COST_WARN_TOKENS:,} 的提醒阈值；如确认要运行，请开启 allow_large_cost。",
        )
    config = {
        "requests": payload.requests,
        "concurrency_levels": levels,
        "stream": payload.stream,
        "max_tokens": payload.max_tokens,
        "temperature": payload.temperature,
        "timeout": payload.timeout,
        "unique_prefix": payload.unique_prefix,
        "http2": payload.http2,
        "logprobs": payload.logprobs,
        "extra_params": {**model_extra, **(payload.extra_params or {})},
        "prompt_tokens_est": prompt_tokens_est,
        "estimated_tokens": estimated_tokens,
        "allow_large_cost": payload.allow_large_cost,
        "prompt_preview": prompt[:1000],
        "prompt_hash": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
    }
    run_id = runner.start_run(
        name=payload.name,
        model_id=model_row["id"],
        model_label=model_row["label"],
        model=model_row["model"],
        conn=conn,
        prompt=prompt,
        config=config,
        preset=payload.preset,
    )
    return {"run_id": run_id}


@router.get("/runs/{run_id}")
def get_run(run_id: int, attempts_limit: int = Query(default=1000, ge=1, le=5000)) -> dict:
    row = store.get_run(run_id)
    if not row:
        raise HTTPException(404, "路由检测任务不存在")
    row["progress"] = runner.progress_for(run_id)
    row["attempts_detail"] = store.list_attempts(run_id, attempts_limit)
    return row


@router.post("/runs/{run_id}/cancel")
def cancel_run(run_id: int) -> dict:
    if not store.get_run(run_id):
        raise HTTPException(404, "路由检测任务不存在")
    return {"ok": runner.cancel(run_id)}


@router.get("/runs/{run_id}/markdown")
def run_markdown(run_id: int) -> dict:
    row = store.get_run(run_id)
    if not row:
        raise HTTPException(404, "路由检测任务不存在")
    report = row.get("summary") or {}
    attempts = store.list_attempts(run_id, 2000)
    config = row.get("config") or {}
    lines = [
        f"# 路由指纹检测报告：{row['name']}",
        "",
        f"- 模型：{row['model_label']}（{row['model']}）",
        f"- 结论：**{report.get('verdict', '—')}**",
        f"- 置信度：{report.get('confidence', 0)}%",
        f"- 样本数：{report.get('total', len(attempts))}",
        f"- 失败数：{report.get('errors', 0)}",
        f"- Prompt SHA256：{config.get('prompt_hash') or '—'}",
        f"- 生成时间：{row.get('finished_at') or row.get('created_at')}",
        "",
        "> 结论为客户端黑盒推断结果，不代表已直接读取上游渠道配置。",
        "",
        "## Prompt 摘要",
        "",
        "```text",
        str(config.get("prompt_preview") or "")[:1000],
        "```",
        "",
        "## 证据摘要",
        "",
    ]
    for reason in report.get("reasons", []):
        lines.append(f"- {reason}")
    if not report.get("reasons"):
        lines.append("- 未发现稳定多组指纹。")
    lines += ["", "## 指纹分组", ""]
    for signal in report.get("signals", []):
        lines.append(f"### {signal.get('name')}")
        lines.append("")
        lines.append(f"- 说明：{signal.get('note') or '无'}")
        lines.append(f"- 置信贡献：{signal.get('strength', 0)}%")
        groups = signal.get("groups") or []
        if not groups:
            lines.append("- 分组：无")
        else:
            lines += ["", "| 指纹值 | 次数 | 占比 | 样例请求 |", "|---|---:|---:|---|"]
            for group in groups:
                sample_ids = "、".join(
                    f"#{s.get('sequence')}({s.get('client_request_id')})"
                    for s in group.get("samples", [])[:3]
                )
                lines.append(
                    f"| {group.get('value') or '<空>'} | {group.get('count')} | "
                    f"{group.get('ratio', 0) * 100:.1f}% | {sample_ids} |"
                )
        lines.append("")
    lines += ["## 原始请求摘要", "",
              "| # | 并发 | HTTP | prompt_tokens | 模型 | ID前缀 | 远端IP | 延迟ms | 错误阶段 | logprobs |",
              "|---:|---:|---:|---:|---|---|---|---:|---|---|"]
    for a in attempts[:200]:
        lines.append(
            f"| {a.get('sequence')} | {a.get('concurrency')} | {a.get('status_code') or ''} | "
            f"{a.get('prompt_tokens') or 0} | {a.get('response_model') or ''} | "
            f"{a.get('id_prefix') or ''} | {a.get('remote_ip') or ''} | "
            f"{a.get('latency_ms') or 0:.1f} | {a.get('error_phase') or ''} | "
            f"{'有' if a.get('logprobs_available') else '—'} |"
        )
    lines += ["", "## 使用限制", "",
              "- 只有失败数量变化、没有稳定指纹时，不能证明是多路由。",
              "- remote_ip/server 变化可能来自 CDN、DNS 或网关多入口。",
              "- prompt_tokens 变化可能来自 tokenizer、chat template 或模型版本差异。",
              "- 建议结合上游渠道日志或单渠道绑定测试进一步确认。"]
    return {"markdown": "\n".join(lines)}


@router.get("/attempts/{attempt_id}")
def get_attempt(attempt_id: int) -> dict:
    row = store.get_attempt(attempt_id)
    if not row:
        raise HTTPException(404, "探测请求不存在")
    return row
