"""基准一致性测试的数据访问层。"""
import json

from ...db import execute, now_iso, query_all, query_one


def create_baseline(name: str, model_label: str, model: str, connection_id: int,
                    case_set: str, dimensions: list[str], config: dict, notes: str = "") -> int:
    return execute(
        "INSERT INTO parity_baselines(name, model_label, model, connection_id, case_set,"
        " dimensions, config_json, status, created_at, notes) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (name, model_label, model, connection_id, case_set, ",".join(dimensions),
         json.dumps(config, ensure_ascii=False), "queued", now_iso(), notes),
    )


def set_baseline_status(baseline_id: int, status: str, error: str = "") -> None:
    execute("UPDATE parity_baselines SET status=?, error=? WHERE id=?", (status, error, baseline_id))


def add_baseline_item(baseline_id: int, item: dict, payload: dict) -> None:
    execute(
        "INSERT INTO parity_baseline_items(baseline_id, case_id, dimension, payload_json,"
        " output_text, raw_response, tool_calls_json, usage_json, status_code, behavior_class,"
        " prompt_tokens, completion_tokens, latency, error) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (baseline_id, item["case_id"], item["dimension"],
         json.dumps(payload, ensure_ascii=False), item.get("output_text", ""),
         item.get("raw_response", ""),
         json.dumps(item.get("tool_calls") or [], ensure_ascii=False),
         json.dumps(item.get("usage") or {}, ensure_ascii=False), item.get("status_code"),
         item.get("behavior_class", ""), item.get("prompt_tokens", 0),
         item.get("completion_tokens", 0), item.get("latency", 0), item.get("error", "")),
    )


def list_baselines() -> list[dict]:
    rows = query_all("SELECT * FROM parity_baselines ORDER BY id DESC")
    for r in rows:
        cnt = query_one("SELECT COUNT(*) AS n FROM parity_baseline_items WHERE baseline_id=?", (r["id"],))
        r["items"] = cnt["n"] if cnt else 0
    return rows


def get_baseline(baseline_id: int) -> dict | None:
    return query_one("SELECT * FROM parity_baselines WHERE id=?", (baseline_id,))


def get_baseline_items(baseline_id: int) -> dict[str, dict]:
    rows = query_all("SELECT * FROM parity_baseline_items WHERE baseline_id=?", (baseline_id,))
    out = {}
    for r in rows:
        out[r["case_id"]] = {**r, "usage": json.loads(r["usage_json"] or "{}"),
                             "tool_calls": json.loads(r.get("tool_calls_json") or "[]")}
    return out


def delete_baseline(baseline_id: int) -> None:
    execute("DELETE FROM parity_baseline_items WHERE baseline_id=?", (baseline_id,))
    execute("DELETE FROM parity_baselines WHERE id=?", (baseline_id,))


def create_run(baseline_id: int, name: str, model_label: str, model: str, connection_id: int,
               dimensions: list[str], config: dict) -> int:
    return execute(
        "INSERT INTO parity_runs(baseline_id, name, model_label, model, connection_id,"
        " dimensions, config_json, status, created_at) VALUES(?,?,?,?,?,?,?,?,?)",
        (baseline_id, name, model_label, model, connection_id, ",".join(dimensions),
         json.dumps(config, ensure_ascii=False), "queued", now_iso()),
    )


def update_run(run_id: int, **fields) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    execute(f"UPDATE parity_runs SET {cols} WHERE id=?", (*fields.values(), run_id))


def add_run_item(run_id: int, item: dict) -> None:
    execute(
        "INSERT INTO parity_run_items(run_id, case_id, dimension, output_text, usage_json,"
        " raw_response, tool_calls_json, status_code, behavior_class, prompt_tokens,"
        " completion_tokens, latency, error) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (run_id, item["case_id"], item["dimension"], item.get("output_text", ""),
         json.dumps(item.get("usage") or {}, ensure_ascii=False),
         item.get("raw_response", ""),
         json.dumps(item.get("tool_calls") or [], ensure_ascii=False), item.get("status_code"),
         item.get("behavior_class", ""), item.get("prompt_tokens", 0),
         item.get("completion_tokens", 0), item.get("latency", 0), item.get("error", "")),
    )


def get_run(run_id: int) -> dict | None:
    return query_one("SELECT * FROM parity_runs WHERE id=?", (run_id,))


def list_runs(limit: int = 50) -> list[dict]:
    return query_all("SELECT id, baseline_id, name, model_label, model, dimensions, status,"
                     " verdict, score, created_at, finished_at FROM parity_runs"
                     " ORDER BY id DESC LIMIT ?", (limit,))


def get_run_items(run_id: int) -> dict[str, dict]:
    rows = query_all("SELECT * FROM parity_run_items WHERE run_id=?", (run_id,))
    out = {}
    for r in rows:
        out[r["case_id"]] = {**r, "usage": json.loads(r["usage_json"] or "{}"),
                             "tool_calls": json.loads(r.get("tool_calls_json") or "[]")}
    return out
