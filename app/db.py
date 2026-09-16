"""SQLite 存取：连接、模型、任务。"""
import sqlite3
from datetime import datetime, timezone

from .settings import DATA_DIR, DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS connections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    base_url TEXT NOT NULL,
    endpoint TEXT NOT NULL DEFAULT '',         -- 接口路径，空=默认 /v1/chat/completions
    key_mode TEXT NOT NULL DEFAULT 'env',      -- env | local
    key_ref TEXT NOT NULL DEFAULT '',          -- env: 变量名; local: 密文
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS models (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    label TEXT NOT NULL,
    model TEXT NOT NULL,
    connection_id INTEGER,
    extra_params TEXT NOT NULL DEFAULT '{}',
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT 'chat',
    config_snapshot TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    total INTEGER NOT NULL DEFAULT 0,
    success INTEGER NOT NULL DEFAULT 0,
    fail INTEGER NOT NULL DEFAULT 0,
    result_dir TEXT,
    summary_path TEXT,
    excel_path TEXT,
    log_path TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS prompts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scenarios (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    prompt TEXT NOT NULL DEFAULT '',
    prompt_file TEXT NOT NULL DEFAULT '',
    overrides TEXT NOT NULL DEFAULT '{}',
    sort INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- ── 基准一致性测试（独立模块，与压测无关）──
CREATE TABLE IF NOT EXISTS parity_baselines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    model_label TEXT NOT NULL,
    model TEXT NOT NULL,
    connection_id INTEGER,
    case_set TEXT NOT NULL,
    dimensions TEXT NOT NULL,          -- 逗号分隔，如 D1,D2,D8,D9
    config_json TEXT NOT NULL,         -- 参数/采样/权重/阈值快照
    status TEXT NOT NULL DEFAULT 'queued',   -- queued/running/ready/failed
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS parity_baseline_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    baseline_id INTEGER NOT NULL,
    case_id TEXT NOT NULL,
    dimension TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    output_text TEXT NOT NULL DEFAULT '',
    raw_response TEXT NOT NULL DEFAULT '',
    tool_calls_json TEXT NOT NULL DEFAULT '[]',
    usage_json TEXT NOT NULL DEFAULT '{}',
    status_code INTEGER,
    behavior_class TEXT NOT NULL DEFAULT '',
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    latency REAL NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS parity_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    baseline_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    model_label TEXT NOT NULL,
    model TEXT NOT NULL,
    connection_id INTEGER,
    dimensions TEXT NOT NULL,
    config_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    verdict TEXT NOT NULL DEFAULT '',
    score REAL,
    report_json TEXT NOT NULL DEFAULT '{}',
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS parity_run_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    case_id TEXT NOT NULL,
    dimension TEXT NOT NULL,
    output_text TEXT NOT NULL DEFAULT '',
    raw_response TEXT NOT NULL DEFAULT '',
    tool_calls_json TEXT NOT NULL DEFAULT '[]',
    usage_json TEXT NOT NULL DEFAULT '{}',
    status_code INTEGER,
    behavior_class TEXT NOT NULL DEFAULT '',
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    latency REAL NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT ''
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def get_conn() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        # ── 轻量迁移：CREATE TABLE IF NOT EXISTS 不会给"已存在的表"补列，
        #    这里按需 ALTER，保证老库升级后不会被新字段卡住 ──
        for table, column, ddl in _COLUMN_MIGRATIONS:
            cols = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
            if cols and column not in cols:          # 表存在但缺列 → 补
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
                print(f"🛠️  数据库迁移：{table} 增加列 {column}")
        conn.commit()


# (表名, 列名, 列定义)：老库升级时按需补齐
_COLUMN_MIGRATIONS = [
    ("connections", "endpoint", "TEXT NOT NULL DEFAULT ''"),
    ("parity_baselines", "status", "TEXT NOT NULL DEFAULT 'queued'"),
    ("parity_baselines", "error", "TEXT NOT NULL DEFAULT ''"),
    ("parity_baseline_items", "tool_calls_json", "TEXT NOT NULL DEFAULT '[]'"),
    ("parity_baseline_items", "raw_response", "TEXT NOT NULL DEFAULT ''"),
    ("parity_run_items", "tool_calls_json", "TEXT NOT NULL DEFAULT '[]'"),
    ("parity_run_items", "raw_response", "TEXT NOT NULL DEFAULT ''"),
]


def query_all(sql: str, params: tuple = ()) -> list[dict]:
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def query_one(sql: str, params: tuple = ()) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(sql, params).fetchone()
        return dict(row) if row else None


def execute(sql: str, params: tuple = ()) -> int:
    """执行写操作，返回 lastrowid（INSERT）或受影响行数。"""
    with get_conn() as conn:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.lastrowid if cur.lastrowid else cur.rowcount
