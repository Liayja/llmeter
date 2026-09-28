"""根据黑盒观测数据推断是否存在多个后端路由，并给出可展示的证据。"""
from collections import Counter, defaultdict


def _group(items: list[dict], value_fn, *, min_count: int = 3,
           min_ratio: float = 0.03) -> list[dict]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for item in items:
        value = str(value_fn(item) or "").strip()
        if value:
            grouped[value].append(item)
    total = len(items)
    if not total:
        return []
    rows = []
    for value, members in sorted(grouped.items(), key=lambda x: len(x[1]), reverse=True):
        count = len(members)
        if count < min_count or count / total < min_ratio:
            continue
        rows.append({
            "value": value,
            "count": count,
            "ratio": round(count / total, 4),
            "samples": [
                {
                    "sequence": a.get("sequence"),
                    "client_request_id": a.get("client_request_id"),
                    "status_code": a.get("status_code"),
                    "prompt_tokens": a.get("prompt_tokens"),
                    "latency_ms": a.get("latency_ms"),
                    "remote_ip": a.get("remote_ip"),
                    "error_phase": a.get("error_phase"),
                }
                for a in members[:5]
            ],
        })
    return rows


def _signal(name: str, items: list[dict], value_fn, strength: int,
            *, note: str = "") -> dict:
    groups = _group(items, value_fn)
    return {
        "name": name,
        "groups": groups,
        "strength": strength if len(groups) > 1 else 0,
        "max_strength": strength,
        "note": note,
    }


def _header(attempt: dict, key: str) -> str:
    headers = attempt.get("response_headers") or {}
    return str(headers.get(key) or headers.get(key.title()) or "")


def analyze_attempts(attempts: list[dict], config: dict | None = None) -> dict:
    config = config or {}
    total = len(attempts)
    errors = [a for a in attempts if a.get("error") or a.get("status_code") is None]
    responses = [a for a in attempts if a.get("response_received")]
    statuses = Counter(a.get("status_code") for a in attempts)
    latencies = sorted(float(a.get("latency_ms") or 0) for a in attempts)
    ttfts = sorted(float(a["ttft_ms"]) for a in attempts if a.get("ttft_ms") is not None)
    unique_prefix = bool(config.get("unique_prefix", True))

    def pct(values: list[float], p: int) -> float | None:
        if not values:
            return None
        idx = min(len(values) - 1, max(0, round((len(values) - 1) * p / 100)))
        return round(values[idx], 1)

    def channel_value(a: dict) -> str:
        channel = _header(a, "x-channel-id")
        upstream = _header(a, "x-upstream-id")
        return f"{channel}|{upstream}" if channel or upstream else ""

    def model_value(a: dict) -> str:
        model = a.get("response_model") or ""
        fingerprint = a.get("system_fingerprint") or ""
        return f"{model}|{fingerprint}" if model or fingerprint else ""

    def edge_value(a: dict) -> str:
        parts = [
            _header(a, "server"), _header(a, "via"), a.get("remote_ip") or "",
            a.get("http_version") or "",
        ]
        return "|".join(parts) if any(parts) else ""

    prompt_note = (
        "唯一前缀开启，prompt_tokens 会有轻微变化，本项只展示、不参与路由结论。"
        if unique_prefix else
        "相同 Prompt 的 prompt_tokens 应保持稳定；出现多组时说明 tokenizer 或模板可能不同。"
    )
    signals = [
        _signal("channel", responses, channel_value, 35,
                note="响应头中的渠道或上游标识，是黑盒条件下最强的直接证据。"),
        _signal("model", responses, model_value, 25,
                note="模型标识或 system_fingerprint 变化，可能说明后端模型实现不同。"),
        _signal(
            "prompt_tokens", responses,
            lambda a: str(a.get("prompt_tokens") or "")
            if int(a.get("prompt_tokens") or 0) > 0 else "",
            25, note=prompt_note,
        ),
        _signal("edge", responses, edge_value, 15,
                note="server、via、远端 IP、HTTP 版本变化，可能是 CDN、网关或上游实例变化。"),
        _signal(
            "id_prefix", responses,
            lambda a: str(a.get("id_prefix") or ""), 10,
            note="响应 ID 前缀变化可能是不同推理框架或网关实现。",
        ),
        _signal(
            "logprobs", responses,
            lambda a: str(a.get("logprobs_hash") or "")
            if a.get("logprobs_available") else "",
            25,
            note="logprobs 向量或哈希稳定分组时，对量化和推理栈差异非常敏感。",
        ),
        _signal("error_phase", attempts,
                lambda a: str(a.get("error_phase") or ""), 10,
                note="区分连接、发送、读取和流式中断阶段。"),
    ]
    if unique_prefix:
        prompt_signal = next(s for s in signals if s["name"] == "prompt_tokens")
        prompt_signal["strength"] = 0
        logprobs_signal = next(s for s in signals if s["name"] == "logprobs")
        if logprobs_signal["groups"]:
            logprobs_signal["strength"] = 0
            logprobs_signal["note"] += " 当前开启唯一前缀，logprobs 不参与多路由结论。"

    confidence_breakdown = [
        {
            "name": s["name"],
            "points": s["strength"],
            "max": s["max_strength"],
            "reason": s["note"],
        }
        for s in signals
    ]
    confidence = min(100, sum(s["strength"] for s in signals))
    strong = [
        s for s in signals
        if s["strength"] and s["name"] in ("channel", "model", "prompt_tokens", "logprobs")
    ]
    channel_signal = next((s for s in signals if s["name"] == "channel"), {"groups": []})
    edge_signal = next((s for s in signals if s["name"] == "edge"), {"groups": []})

    if total < 30:
        verdict = "INCONCLUSIVE"
    elif len(channel_signal["groups"]) > 1:
        verdict = "MULTI_ROUTE_LIKELY"
    elif len(strong) >= 2:
        verdict = "MULTI_ROUTE_LIKELY"
    elif len(strong) == 1:
        verdict = "MULTI_ROUTE_SUSPECTED"
    elif len(next((s for s in signals if s["name"] == "id_prefix"), {"groups": []})["groups"]) > 1:
        verdict = "MULTI_INSTANCE_LIKELY"
    elif len(edge_signal["groups"]) > 1:
        verdict = "EDGE_VARIANCE_LIKELY"
    elif errors:
        verdict = "CLIENT_OR_GATEWAY_UNSTABLE"
    else:
        verdict = "SINGLE_ROUTE"

    evidence = []
    for signal in signals:
        if len(signal["groups"]) <= 1:
            continue
        evidence.append({
            "dimension": signal["name"],
            "summary": signal["note"],
            "groups": signal["groups"],
            "contribution": signal["strength"],
        })

    reasons = []
    for signal in signals:
        if len(signal["groups"]) <= 1:
            continue
        parts = [
            f"{g['value'] or '<空>'}: {g['count']} 次（{g['ratio'] * 100:.1f}%）"
            for g in signal["groups"][:5]
        ]
        reasons.append(f"{signal['name']} 出现多组指纹：{'；'.join(parts)}")
    if unique_prefix:
        reasons.append("本次开启了唯一前缀，因此 prompt_tokens 不作为多路由结论依据。")
    if errors and not any(s["groups"] for s in signals):
        reasons.append("存在连接/读取失败，但没有形成稳定的 token、模型或响应头指纹分组，不能仅凭失败判断多路由。")

    return {
        "verdict": verdict,
        "confidence": confidence,
        "evidence_quality": (
            "strong" if confidence >= 50 or len(channel_signal["groups"]) > 1
            else "medium" if confidence >= 25
            else "weak"
        ),
        "confidence_breakdown": confidence_breakdown,
        "total": total,
        "errors": len(errors),
        "status_counts": {str(k): v for k, v in statuses.items()},
        "latency_ms": {"p50": pct(latencies, 50), "p95": pct(latencies, 95),
                       "max": pct(latencies, 100)},
        "ttft_ms": {"p50": pct(ttfts, 50), "p95": pct(ttfts, 95)} if ttfts else None,
        "signals": signals,
        "evidence": evidence,
        "reasons": reasons,
    }
