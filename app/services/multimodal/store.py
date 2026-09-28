"""多模态媒体资产与运行记录的数据访问层。"""
import json

from ...db import execute, now_iso, query_all, query_one


def create_media(*, name: str, kind: str, source_type: str, mime_type: str = "",
                 size_bytes: int = 0, sha256: str = "", local_path: str = "",
                 remote_url: str = "", metadata: dict | None = None) -> int:
    return execute(
        "INSERT INTO mm_media(name, kind, source_type, mime_type, size_bytes, sha256,"
        " local_path, remote_url, metadata_json, created_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?)",
        (name, kind, source_type, mime_type, size_bytes, sha256, local_path,
         remote_url, json.dumps(metadata or {}, ensure_ascii=False), now_iso()),
    )


def find_media_by_sha(sha256: str) -> dict | None:
    if not sha256:
        return None
    return query_one("SELECT * FROM mm_media WHERE sha256=? ORDER BY id LIMIT 1", (sha256,))


def list_media() -> list[dict]:
    rows = query_all("SELECT * FROM mm_media ORDER BY id DESC")
    for row in rows:
        row["metadata"] = json.loads(row.get("metadata_json") or "{}")
    return rows


def get_media(media_id: int) -> dict | None:
    row = query_one("SELECT * FROM mm_media WHERE id=?", (media_id,))
    if row:
        row["metadata"] = json.loads(row.get("metadata_json") or "{}")
    return row


def delete_media(media_id: int) -> None:
    execute("DELETE FROM mm_media WHERE id=?", (media_id,))


def create_run(*, name: str, model_id: int | None, model_label: str, model: str,
               connection_id: int | None, media_id: int, media_kind: str,
               input_mode: str, prompt: str, expected_keywords: list[str],
               config: dict) -> int:
    return execute(
        "INSERT INTO mm_runs(name, model_id, model_label, model, connection_id, media_id,"
        " media_kind, input_mode, prompt, expected_keywords_json, config_json, status, created_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (name, model_id, model_label, model, connection_id, media_id, media_kind,
         input_mode, prompt, json.dumps(expected_keywords or [], ensure_ascii=False),
         json.dumps(config or {}, ensure_ascii=False), "queued", now_iso()),
    )


def update_run(run_id: int, **fields) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    execute(f"UPDATE mm_runs SET {cols} WHERE id=?", (*fields.values(), run_id))


def add_run_item(run_id: int, item: dict, request_payload: dict) -> None:
    execute(
        "INSERT INTO mm_run_items(run_id, request_json, raw_response, raw_response_truncated,"
        " output_text, output_text_truncated, request_size_bytes, usage_json, status_code, behavior_class,"
        " prompt_tokens, completion_tokens, latency, ttft, verdict, reason, error)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            run_id, json.dumps(request_payload, ensure_ascii=False),
            item.get("raw_response", ""), int(bool(item.get("raw_response_truncated"))),
            item.get("output_text", ""), int(bool(item.get("output_text_truncated"))),
            item.get("request_size_bytes", 0),
            json.dumps(item.get("usage") or {}, ensure_ascii=False),
            item.get("status_code"), item.get("behavior_class", ""),
            item.get("prompt_tokens", 0), item.get("completion_tokens", 0),
            item.get("latency", 0), item.get("ttft"), item.get("verdict", ""),
            item.get("reason", ""), item.get("error", ""),
        ),
    )


def get_run(run_id: int) -> dict | None:
    row = query_one("SELECT * FROM mm_runs WHERE id=?", (run_id,))
    if not row:
        return None
    item = query_one("SELECT * FROM mm_run_items WHERE run_id=? ORDER BY id LIMIT 1", (run_id,))
    if item:
        try:
            item["request"] = json.loads(item.get("request_json") or "{}")
        except Exception:      # noqa: BLE001
            item["request"] = {}
        try:
            item["usage"] = json.loads(item.get("usage_json") or "{}")
        except Exception:      # noqa: BLE001
            item["usage"] = {}
    row["item"] = item
    row["expected_keywords"] = json.loads(row.get("expected_keywords_json") or "[]")
    row["config"] = json.loads(row.get("config_json") or "{}")
    return row


def list_runs(limit: int = 50) -> list[dict]:
    return query_all(
        "SELECT id, name, model_label, model, media_id, media_kind, input_mode, status,"
        " verdict, created_at, finished_at FROM mm_runs ORDER BY id DESC LIMIT ?",
        (limit,),
    )


def mark_stale_runs() -> None:
    execute(
        "UPDATE mm_runs SET status='failed', error=?, finished_at=? "
        "WHERE status IN ('queued','running')",
        ("服务重启，任务已中断；请重新运行", now_iso()),
    )
