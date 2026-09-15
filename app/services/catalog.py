"""资产与任务的数据库操作。"""
import json

from ..db import execute, now_iso, query_all, query_one


# ── 连接 ────────────────────────────────────────────────
def list_connections() -> list[dict]:
    return query_all("SELECT * FROM connections ORDER BY id DESC")


def get_connection(conn_id: int) -> dict | None:
    return query_one("SELECT * FROM connections WHERE id=?", (conn_id,))


def create_connection(name: str, base_url: str, key_mode: str, key_ref: str,
                      note: str = "", endpoint: str = "") -> int:
    ts = now_iso()
    return execute(
        "INSERT INTO connections(name, base_url, endpoint, key_mode, key_ref, note, created_at, updated_at)"
        " VALUES(?,?,?,?,?,?,?,?)",
        (name, base_url, endpoint, key_mode, key_ref, note, ts, ts),
    )


def update_connection(conn_id: int, name: str, base_url: str, key_mode: str,
                      key_ref: str, note: str = "", endpoint: str = "") -> None:
    execute(
        "UPDATE connections SET name=?, base_url=?, endpoint=?, key_mode=?, key_ref=?, note=?,"
        " updated_at=? WHERE id=?",
        (name, base_url, endpoint, key_mode, key_ref, note, now_iso(), conn_id),
    )


def delete_connection(conn_id: int) -> None:
    execute("DELETE FROM connections WHERE id=?", (conn_id,))
    execute("UPDATE models SET connection_id=NULL WHERE connection_id=?", (conn_id,))


# ── 模型 ────────────────────────────────────────────────
def list_models() -> list[dict]:
    return query_all("SELECT * FROM models ORDER BY id DESC")


def get_model(model_id: int) -> dict | None:
    return query_one("SELECT * FROM models WHERE id=?", (model_id,))


def create_model(label: str, model: str, connection_id: int | None,
                 extra_params: dict | None = None, note: str = "") -> int:
    ts = now_iso()
    return execute(
        "INSERT INTO models(label, model, connection_id, extra_params, note, created_at, updated_at)"
        " VALUES(?,?,?,?,?,?,?)",
        (label, model, connection_id, json.dumps(extra_params or {}, ensure_ascii=False), note, ts, ts),
    )


def update_model(model_id: int, label: str, model: str, connection_id: int | None,
                 extra_params: dict | None = None, note: str = "") -> None:
    execute(
        "UPDATE models SET label=?, model=?, connection_id=?, extra_params=?, note=?, updated_at=?"
        " WHERE id=?",
        (label, model, connection_id, json.dumps(extra_params or {}, ensure_ascii=False),
         note, now_iso(), model_id),
    )


def delete_model(model_id: int) -> None:
    execute("DELETE FROM models WHERE id=?", (model_id,))


# ── 任务 ────────────────────────────────────────────────
def create_task(name: str, mode: str, config_snapshot: dict) -> int:
    return execute(
        "INSERT INTO tasks(name, mode, config_snapshot, status, created_at) VALUES(?,?,?,?,?)",
        (name, mode, json.dumps(config_snapshot, ensure_ascii=False), "queued", now_iso()),
    )


def get_task(task_id: int) -> dict | None:
    return query_one("SELECT * FROM tasks WHERE id=?", (task_id,))


def list_tasks(limit: int = 50) -> list[dict]:
    return query_all("SELECT * FROM tasks ORDER BY id DESC LIMIT ?", (limit,))


def update_task(task_id: int, **fields) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    execute(f"UPDATE tasks SET {cols} WHERE id=?", (*fields.values(), task_id))


# ── 提示词 ──────────────────────────────────────────────
def list_prompts() -> list[dict]:
    return query_all("SELECT * FROM prompts ORDER BY id DESC")


def get_prompt(prompt_id: int) -> dict | None:
    return query_one("SELECT * FROM prompts WHERE id=?", (prompt_id,))


def create_prompt(name: str, content: str, tags: str = "") -> int:
    ts = now_iso()
    return execute("INSERT INTO prompts(name, content, tags, created_at, updated_at)"
                   " VALUES(?,?,?,?,?)", (name, content, tags, ts, ts))


def update_prompt(prompt_id: int, name: str, content: str, tags: str = "") -> None:
    execute("UPDATE prompts SET name=?, content=?, tags=?, updated_at=? WHERE id=?",
            (name, content, tags, now_iso(), prompt_id))


def delete_prompt(prompt_id: int) -> None:
    execute("DELETE FROM prompts WHERE id=?", (prompt_id,))


# ── 场景 ────────────────────────────────────────────────
def list_scenarios() -> list[dict]:
    return query_all("SELECT * FROM scenarios ORDER BY sort, id")


def get_scenario(scenario_id: int) -> dict | None:
    return query_one("SELECT * FROM scenarios WHERE id=?", (scenario_id,))


def create_scenario(name: str, prompt: str, prompt_file: str = "",
                    overrides: dict | None = None, sort: int = 0) -> int:
    ts = now_iso()
    return execute(
        "INSERT INTO scenarios(name, prompt, prompt_file, overrides, sort, created_at, updated_at)"
        " VALUES(?,?,?,?,?,?,?)",
        (name, prompt, prompt_file, json.dumps(overrides or {}, ensure_ascii=False), sort, ts, ts),
    )


def update_scenario(scenario_id: int, name: str, prompt: str, prompt_file: str = "",
                    overrides: dict | None = None, sort: int = 0) -> None:
    execute(
        "UPDATE scenarios SET name=?, prompt=?, prompt_file=?, overrides=?, sort=?, updated_at=?"
        " WHERE id=?",
        (name, prompt, prompt_file, json.dumps(overrides or {}, ensure_ascii=False),
         sort, now_iso(), scenario_id),
    )


def delete_scenario(scenario_id: int) -> None:
    execute("DELETE FROM scenarios WHERE id=?", (scenario_id,))
