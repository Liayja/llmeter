"""路由指纹检测的数据访问层。"""
import json

from ...db import execute, now_iso, query_all, query_one


def create_run(*, name: str, model_id: int | None, model_label: str, model: str,
               connection_id: int | None, preset: str, config: dict) -> int:
    return execute(
        "INSERT INTO route_probe_runs(name, model_id, model_label, model, connection_id,"
        " preset, config_json, status, created_at) VALUES(?,?,?,?,?,?,?,?,?)",
        (name, model_id, model_label, model, connection_id, preset,
         json.dumps(config or {}, ensure_ascii=False), "queued", now_iso()),
    )


def update_run(run_id: int, **fields) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    execute(f"UPDATE route_probe_runs SET {cols} WHERE id=?", (*fields.values(), run_id))


def add_attempt(run_id: int, item: dict) -> int:
    return execute(
        "INSERT INTO route_probe_attempts(run_id, sequence, concurrency, client_request_id,"
        " phase, status_code, error_type, error_phase, response_received, remote_ip,"
        " http_version, ttfe_ms, ttft_ms, latency_ms, request_size_bytes,"
        " response_headers_json, response_model, system_fingerprint, response_id, id_prefix,"
        " usage_json, prompt_tokens, completion_tokens, finish_reason, output_hash,"
        " logprobs_hash, logprobs_available, request_json, raw_response,"
        " raw_response_truncated, error, created_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            run_id, item.get("sequence", 0), item.get("concurrency", 1),
            item.get("client_request_id", ""), item.get("phase", ""),
            item.get("status_code"), item.get("error_type", ""),
            item.get("error_phase", ""), int(bool(item.get("response_received"))),
            item.get("remote_ip", ""), item.get("http_version", ""),
            item.get("ttfe_ms"), item.get("ttft_ms"), item.get("latency_ms", 0),
            item.get("request_size_bytes", 0),
            json.dumps(item.get("response_headers") or {}, ensure_ascii=False),
            item.get("response_model", ""), item.get("system_fingerprint", ""),
            item.get("response_id", ""), item.get("id_prefix", ""),
            json.dumps(item.get("usage") or {}, ensure_ascii=False),
            item.get("prompt_tokens", 0), item.get("completion_tokens", 0),
            item.get("finish_reason", ""), item.get("output_hash", ""),
            item.get("logprobs_hash", ""), int(bool(item.get("logprobs_available"))),
            json.dumps(item.get("request") or {}, ensure_ascii=False),
            item.get("raw_response", ""), int(bool(item.get("raw_response_truncated"))),
            item.get("error", ""), now_iso(),
        ),
    )


def get_run(run_id: int) -> dict | None:
    row = query_one("SELECT * FROM route_probe_runs WHERE id=?", (run_id,))
    if row:
        row["config"] = json.loads(row.get("config_json") or "{}")
        row["summary"] = json.loads(row.get("summary_json") or "{}")
    return row


def list_runs(limit: int = 50) -> list[dict]:
    rows = query_all(
        "SELECT id, name, model_label, model, preset, status, verdict, confidence,"
        " created_at, finished_at FROM route_probe_runs ORDER BY id DESC LIMIT ?",
        (limit,),
    )
    for row in rows:
        row["attempts"] = query_one(
            "SELECT COUNT(*) AS n FROM route_probe_attempts WHERE run_id=?",
            (row["id"],),
        )["n"]
    return rows


def list_attempts(run_id: int, limit: int = 2000) -> list[dict]:
    rows = query_all(
        "SELECT id, sequence, concurrency, client_request_id, phase, status_code,"
        " error_type, error_phase, response_received, remote_ip, http_version,"
        " ttfe_ms, ttft_ms, latency_ms, request_size_bytes, response_headers_json,"
        " response_model, system_fingerprint, usage_json, prompt_tokens, completion_tokens,"
        " response_id, id_prefix, finish_reason, output_hash, logprobs_hash,"
        " logprobs_available, error, created_at"
        " FROM route_probe_attempts WHERE run_id=? ORDER BY sequence LIMIT ?",
        (run_id, limit),
    )
    for row in rows:
        row["response_received"] = bool(row.get("response_received"))
        row["logprobs_available"] = bool(row.get("logprobs_available"))
        row["response_headers"] = json.loads(row.get("response_headers_json") or "{}")
        row["usage"] = json.loads(row.get("usage_json") or "{}")
    return rows


def get_attempt(attempt_id: int) -> dict | None:
    row = query_one("SELECT * FROM route_probe_attempts WHERE id=?", (attempt_id,))
    if not row:
        return None
    row["response_received"] = bool(row.get("response_received"))
    row["response_headers"] = json.loads(row.get("response_headers_json") or "{}")
    row["usage"] = json.loads(row.get("usage_json") or "{}")
    try:
        row["request"] = json.loads(row.get("request_json") or "{}")
    except Exception:      # noqa: BLE001
        row["request"] = {}
    return row


def mark_stale_runs() -> None:
    execute(
        "UPDATE route_probe_runs SET status='failed', error=?, finished_at=? "
        "WHERE status IN ('queued','running')",
        ("服务重启，路由检测已中断", now_iso()),
    )
