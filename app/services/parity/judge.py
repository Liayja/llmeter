"""一致性判定：闸门校验 → 逐用例判定 → 维度聚合 → 结论（等价/可疑/不等价/不可比）。"""
import json
import re

from .cases import CASE_SET, DIMENSIONS

# 默认阈值（可在 config 中覆盖；初始值见设计文档 7.4）
DEFAULT_CONFIG = {
    "enable_weighted_score": True,
    "enable_red_flags": True,
    "baseline_max_age_days": 30,
    "weights": {k: v["weight"] for k, v in DIMENSIONS.items()},
    "thresholds": {
        "D1_max_tokens_violation": [5, 20],      # %：≤5 绿 / ≤20 黄 / >20 红
        "D2_tokenizer_deviation": [1, 2],        # %：≤1 绿 / ≤2 黄 / >2 红
        "D8_pass_rate_diff": [5, 10],            # pp
        "D9_behavior_match": [95, 80],           # %：≥95 绿 / ≥80 黄 / <80 红
    },
}


def _num(v, default=0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _strip_fences(text: str) -> str:
    t = (text or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\n?", "", t)
        t = re.sub(r"\n?```$", "", t)
    return t.strip()


_OPERATIONAL_STATUS = {401, 403, 429}


def _status(item: dict) -> int | None:
    value = (item or {}).get("status_code")
    return value if isinstance(value, int) else None


def _operational_failure(item: dict) -> bool:
    """鉴权、限流、服务端错误和连接失败都不属于模型行为差异。"""
    if not item:
        return True
    status = _status(item)
    if item.get("error") and status is None:
        return True
    if status is None or status >= 500 or status in _OPERATIONAL_STATUS:
        return True
    return False


def _baseline_valid(case: dict, item: dict) -> bool:
    """官方基线必须提供可用证据；D9 的 4xx 是预期行为，其他维度要求 2xx。"""
    if _operational_failure(item):
        return False
    status = _status(item)
    if case.get("dimension") == "D9":
        return status is not None and 200 <= status < 500
    return status is not None and 200 <= status < 300


def _fail_reason(status: int | None) -> str:
    if status is None:
        return "候选没有返回 HTTP 状态"
    return f"候选返回 HTTP {status}，期望成功响应"


# ── 逐用例判定 ────────────────────────────────────────────────
def judge_case(case: dict, ref: dict, cand: dict) -> dict:
    """返回 {verdict: pass/fail/skip, reason: str, metric: float|None}"""
    rule = case.get("judge")
    if not _baseline_valid(case, ref):
        return {"verdict": "inconclusive", "reason": "官方基线证据不可用", "metric": None}
    if _operational_failure(cand):
        reason = "候选请求异常"
        if cand.get("error"):
            reason += f"：{str(cand.get('error'))[:120]}"
        elif _status(cand) is not None:
            reason += f"：HTTP {_status(cand)}"
        return {"verdict": "inconclusive", "reason": reason, "metric": None}
    if case.get("dimension") != "D9" and not (
        _status(cand) is not None and 200 <= _status(cand) < 300
    ):
        return {"verdict": "fail", "reason": _fail_reason(_status(cand)), "metric": None}

    if rule == "tokenizer_fingerprint":
        base_tok, cand_tok = _num(ref.get("prompt_tokens")), _num(cand.get("prompt_tokens"))
        if base_tok <= 0 or cand_tok <= 0:
            return {"verdict": "skip", "reason": "缺少 prompt_tokens，无法比对", "metric": None}
        dev = abs(cand_tok - base_tok) / base_tok * 100
        return {"verdict": "pass" if dev <= 2 else "fail",
                "reason": f"prompt_tokens 基线 {int(base_tok)} / 候选 {int(cand_tok)}，偏差 {dev:.2f}%",
                "metric": dev}

    if rule == "max_tokens_compliance":
        limit = case["payload"].get("max_tokens", 0)
        used = _num(cand.get("completion_tokens"))
        if not cand.get("usage"):
            return {"verdict": "skip", "reason": "候选未返回 usage，无法判断是否遵守 max_tokens", "metric": None}
        if limit and used > limit:
            return {"verdict": "fail",
                    "reason": f"请求 max_tokens={limit}，实际输出 {int(used)} token", "metric": used - limit}
        return {"verdict": "pass", "reason": f"max_tokens={limit}，实际输出 {int(used)} token", "metric": 0}

    if rule == "reasoning_field":
        def has_reasoning(item: dict) -> bool:
            usage = item.get("usage") or {}
            details = usage.get("completion_tokens_details") or {}
            return "reasoning_tokens" in details
        base_has, cand_has = has_reasoning(ref), has_reasoning(cand)
        if base_has and not cand_has:
            return {"verdict": "fail", "reason": "官方返回 reasoning_tokens，候选没有该字段", "metric": None}
        return {"verdict": "pass",
                "reason": f"reasoning_tokens 字段：官方 {'有' if base_has else '无'} / 候选 {'有' if cand_has else '无'}",
                "metric": None}

    if rule == "system_role":
        def cjk_ratio(t: str) -> float:
            t = t or ""
            if not t:
                return 0.0
            return sum(1 for c in t if "\u4e00" <= c <= "\u9fff") / len(t)
        cand_ratio = cjk_ratio(cand.get("output_text", ""))
        ok = cand_ratio < 0.05
        return {"verdict": "pass" if ok else "fail",
                "reason": f"要求只用英文，候选中文占比 {cand_ratio * 100:.1f}%", "metric": cand_ratio}

    if rule == "json_output":
        raw = _strip_fences(cand.get("output_text", ""))
        try:
            obj = json.loads(raw)
        except Exception:      # noqa: BLE001
            return {"verdict": "fail", "reason": f"输出不是合法 JSON：{raw[:80]!r}", "metric": None}
        need = {"city", "weather", "temp_c"}
        missing = need - set(obj) if isinstance(obj, dict) else need
        if missing:
            return {"verdict": "fail", "reason": f"JSON 缺少字段：{sorted(missing)}", "metric": None}
        return {"verdict": "pass", "reason": "JSON 解析成功且字段齐全", "metric": None}

    if rule == "tool_call":
        calls = cand.get("tool_calls") or []
        if not calls:
            return {"verdict": "fail", "reason": "候选没有返回 tool_calls（工具调用不可用）", "metric": None}
        name = ((calls[0] or {}).get("function") or {}).get("name")
        args = ((calls[0] or {}).get("function") or {}).get("arguments")
        if name != "get_weather":
            return {"verdict": "fail", "reason": f"调用了错误的工具：{name}", "metric": None}
        try:
            parsed = json.loads(args or "{}")
        except Exception:      # noqa: BLE001
            return {"verdict": "fail", "reason": f"工具参数不是合法 JSON：{str(args)[:80]}", "metric": None}
        if "city" not in parsed:
            return {"verdict": "fail", "reason": f"工具参数缺少 city：{parsed}", "metric": None}
        return {"verdict": "pass", "reason": f"工具调用正确：get_weather({parsed})", "metric": None}

    if rule == "behavior_class":
        base_cls, cand_cls = ref.get("behavior_class", ""), cand.get("behavior_class", "")
        if base_cls in ("ERROR_OTHER", "UNKNOWN") or cand_cls in ("ERROR_OTHER", "UNKNOWN"):
            return {"verdict": "inconclusive",
                    "reason": f"行为类别包含服务异常或无响应：官方 {base_cls} / 候选 {cand_cls}",
                    "metric": None}
        same = base_cls == cand_cls
        reason = f"行为类别：官方 {base_cls} / 候选 {cand_cls}"
        if same and cand_cls == "ACCEPT_AS_IS":
            reason += "（均接受；注意：无法排除静默修复，P2 将用合法变体比对 prompt_tokens 进一步判定）"
        return {"verdict": "pass" if same else "fail", "reason": reason, "metric": None}

    return {"verdict": "skip", "reason": f"未实现的判定规则：{rule}", "metric": None}


# ── 维度聚合 ──────────────────────────────────────────────────
def _grade(value: float, thresholds: list, higher_better: bool = False) -> str:
    lo, hi = thresholds
    if higher_better:
        return "green" if value >= lo else ("yellow" if value >= hi else "red")
    return "green" if value <= lo else ("yellow" if value <= hi else "red")


def aggregate_dimension(dim: str, results: list[dict], cfg: dict) -> dict:
    name = DIMENSIONS.get(dim, {}).get("name", dim)
    graded = [r for r in results if r["verdict"] in ("pass", "fail")]
    total = len(graded)
    passed = sum(1 for r in graded if r["verdict"] == "pass")
    inconclusive = sum(1 for r in results if r["verdict"] == "inconclusive")
    metrics: dict = {
        "cases": len(results),
        "graded": total,
        "passed": passed,
        "inconclusive": inconclusive,
    }
    thresholds = cfg.get("thresholds", {})

    if dim == "D1":
        max_rows = [r for r in graded if r["case_id"] == "D1-max-tokens"]
        viol = [r for r in max_rows if r["verdict"] == "fail"]
        rate = (len(viol) / len(max_rows) * 100) if max_rows else 0
        metrics["max_tokens_violation_rate"] = round(rate, 1)
        metrics["max_tokens_evidence"] = len(max_rows)
        if not max_rows:
            verdict = "gray"
        else:
            rate_verdict = _grade(rate, thresholds.get("D1_max_tokens_violation", [5, 20]))
            pass_rate = (passed / total * 100) if total else 0
            metrics["pass_rate"] = round(pass_rate, 1)
            verdict = rate_verdict if rate_verdict != "green" else (
                "green" if pass_rate == 100 else "yellow"
            )
    elif dim == "D2":
        devs = [r["metric"] for r in graded if r.get("metric") is not None]
        if not devs:
            verdict = "gray"
        else:
            mean_dev = sum(devs) / len(devs)
            metrics["tokenizer_mean_deviation"] = round(mean_dev, 2)
            metrics["tokenizer_max_deviation"] = round(max(devs), 2)
            verdict = _grade(mean_dev, thresholds.get("D2_tokenizer_deviation", [1, 2]))
    elif dim == "D8":
        valid = [
            r for r in results
            if r["verdict"] in ("pass", "fail") and r.get("ref_verdict") in ("pass", "fail")
        ]
        if not valid:
            verdict = "gray"
        else:
            cand_rate = sum(1 for r in valid if r["verdict"] == "pass") / len(valid) * 100
            base_rate = sum(1 for r in valid if r.get("ref_verdict") == "pass") / len(valid) * 100
            diff = base_rate - cand_rate
            metrics["candidate_pass_rate"] = round(cand_rate, 1)
            metrics["baseline_pass_rate"] = round(base_rate, 1)
            metrics["pass_rate_diff"] = round(diff, 1)
            verdict = _grade(diff, thresholds.get("D8_pass_rate_diff", [5, 10]))
    elif dim == "D9":
        if not graded:
            verdict = "gray"
        else:
            match_rate = (passed / total * 100) if total else 0
            metrics["behavior_match_rate"] = round(match_rate, 1)
            verdict = _grade(
                match_rate, thresholds.get("D9_behavior_match", [95, 80]), higher_better=True,
            )
    else:
        verdict = "gray"

    return {"dimension": dim, "name": name, "verdict": verdict, "metrics": metrics,
            "cases": results}


def build_report(*, baseline: dict, run: dict, cases: list[dict], ref_items: dict,
                 cand_items: dict, cfg: dict, baseline_age_days: float | None = None) -> dict:
    """生成完整报告：闸门 → 逐用例 → 维度 → 红旗 → 结论。"""
    # ── 闸门（D0）──
    gate_reasons: list[str] = []
    gate_suggestions: list[str] = []
    baseline_valid = [
        c["id"] for c in cases
        if _baseline_valid(c, ref_items.get(c["id"], {}))
    ]
    baseline_invalid = [c["id"] for c in cases if c["id"] not in baseline_valid]
    candidate_inconclusive = [
        c["id"] for c in cases if _operational_failure(cand_items.get(c["id"], {}))
    ]
    coverage = len(baseline_valid) / len(cases) * 100 if cases else 0
    if baseline.get("case_set") != CASE_SET:
        gate_reasons.append(f"用例集不一致：基线 {baseline.get('case_set')} / 当前 {CASE_SET}")
        gate_suggestions.append("重选基线或重建基线")
    if coverage < 95:
        gate_reasons.append(
            f"基线可用证据不足：{len(baseline_valid)}/{len(cases)}"
            f"（问题用例 {', '.join(baseline_invalid[:5])} …）"
        )
        gate_suggestions.append("重建基线，并确认鉴权、限流、超时和服务端状态正常")
    if baseline_age_days is not None and baseline_age_days > cfg.get("baseline_max_age_days", 30):
        gate_reasons.append(f"基线创建于 {baseline_age_days:.0f} 天前，官方模型可能已变更")
        gate_suggestions.append("建议重建基线")
    if candidate_inconclusive:
        gate_reasons.append(
            f"候选存在 {len(candidate_inconclusive)} 条不可判断请求"
            f"（鉴权/限流/5xx/超时）：{', '.join(candidate_inconclusive[:5])} …"
        )
        gate_suggestions.append("检查候选连接的 Base URL / 接口路径 / API Key、限流和服务端状态")
    gate_ok = not gate_reasons

    # ── 逐用例判定 ──
    case_results: list[dict] = []
    for case in cases:
        ref = ref_items.get(case["id"], {})
        cand = cand_items.get(case["id"], {})
        ref_verdict = judge_case(case, ref, ref)      # 基线自身是否满足该用例的"期望"
        res = judge_case(case, ref, cand)
        if not _baseline_valid(case, ref):
            evidence_status = "baseline_invalid"
        elif _operational_failure(cand):
            evidence_status = "candidate_inconclusive"
        else:
            evidence_status = "comparable"
        case_results.append({
            "case_id": case["id"], "dimension": case["dimension"], "name": case["name"],
            "request": case.get("payload", {}),          # 实际发出的请求体（两侧一致）
            "verdict": res["verdict"], "reason": res["reason"], "metric": res.get("metric"),
            "ref_verdict": ref_verdict["verdict"],
            "evidence_status": evidence_status,
            "baseline": {k: ref.get(k) for k in ("status_code", "behavior_class", "prompt_tokens",
                                                "completion_tokens", "output_text", "raw_response",
                                                "raw_response_truncated", "output_text_truncated",
                                                "param_notes", "usage", "latency", "error")},
            "candidate": {k: cand.get(k) for k in ("status_code", "behavior_class", "prompt_tokens",
                                                  "completion_tokens", "output_text", "raw_response",
                                                  "raw_response_truncated", "output_text_truncated",
                                                  "param_notes", "usage", "error", "latency")},
        })

    # ── 维度聚合 ──
    dims = []
    for dim in [d for d in run.get("dimensions", []) if d in DIMENSIONS]:
        rows = [r for r in case_results if r["dimension"] == dim]
        if rows:
            dims.append(aggregate_dimension(dim, rows, cfg))
    gray_dims = [d["dimension"] for d in dims if d["verdict"] == "gray"]
    if gray_dims:
        gate_reasons.append(
            f"以下维度缺少可判定证据：{', '.join(gray_dims)}"
        )
        gate_suggestions.append("检查缺失的 usage/输出字段，并重新运行候选测试")
        gate_ok = False

    # ── 红旗（一票否决）──
    red_flags: list[str] = []
    if cfg.get("enable_red_flags", True):
        d2 = next((d for d in dims if d["dimension"] == "D2"), None)
        if d2 and d2["metrics"].get("tokenizer_max_deviation", 0) > 2:
            red_flags.append(f"tokenizer 指纹偏差过大（最大 {d2['metrics']['tokenizer_max_deviation']}%）："
                             "上游很可能不是同一套 tokenizer / 模板实现")
        d1 = next((d for d in dims if d["dimension"] == "D1"), None)
        if d1 and d1["metrics"].get("max_tokens_violation_rate", 0) > 20:
            red_flags.append(f"max_tokens 系统性不遵守（违规率 {d1['metrics']['max_tokens_violation_rate']}%）")
        for cid in ("D9-orphan-tool", "D9-id-mismatch", "D9-dangling-call"):
            row = next((r for r in case_results if r["case_id"] == cid), None)
            if row and row["verdict"] == "fail":
                red_flags.append(f"孤儿/异常 tool 序列处理与官方不同：{row['name']}（"
                                 f"官方 {row['baseline']['behavior_class']} / 候选 {row['candidate']['behavior_class']}）")
        d8 = next((d for d in dims if d["dimension"] == "D8"), None)
        if d8 and d8["metrics"].get("candidate_pass_rate", 100) == 0 and d8["metrics"].get("baseline_pass_rate", 0) > 0:
            red_flags.append("工具调用/结构输出完全不可用（候选通过率 0%）")

    # ── 结论 ──
    yellow_count = sum(1 for d in dims if d["verdict"] == "yellow")
    red_count = sum(1 for d in dims if d["verdict"] == "red")
    gray_count = sum(1 for d in dims if d["verdict"] == "gray")
    if not gate_ok:
        verdict = "不可比"
    elif gray_count:
        verdict = "不可比"
    elif red_flags or red_count:
        verdict = "不等价"
    elif yellow_count >= 2:
        verdict = "可疑"
    else:
        verdict = "等价"

    score = None
    if cfg.get("enable_weighted_score", True) and dims:
        weights = cfg.get("weights", {})
        dim_score = {"green": 100.0, "yellow": 60.0, "red": 0.0}
        scored = [d for d in dims if d["verdict"] in dim_score]
        wsum = sum(_num(weights.get(d["dimension"], 0)) for d in scored) or 1
        if scored:
            score = round(
                sum(dim_score[d["verdict"]] * _num(weights.get(d["dimension"], 0)) for d in scored)
                / wsum,
                1,
            )

    return {
        "verdict": verdict,
        "score": score,
        "gate": {"ok": gate_ok, "reasons": gate_reasons, "suggestions": gate_suggestions,
                 "coverage": round(coverage, 1),
                 "baseline_invalid": baseline_invalid,
                 "candidate_inconclusive": candidate_inconclusive},
        "red_flags": red_flags,
        "dimensions": [{k: v for k, v in d.items() if k != "cases"} for d in dims],
        "cases": case_results,
        "config": cfg,
        "baseline": {"id": baseline.get("id"), "name": baseline.get("name"),
                     "model": baseline.get("model"), "case_set": baseline.get("case_set"),
                     "created_at": baseline.get("created_at")},
        "candidate": {"model": run.get("model"), "label": run.get("model_label")},
    }
