"""平台全局配置：路径与默认值。"""
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = APP_DIR.parent

DATA_DIR = APP_DIR / "data"
DB_PATH = DATA_DIR / "llmeter.db"
MM_MEDIA_DIR = DATA_DIR / "mm_media"
STATIC_DIR = APP_DIR / "static"
# Vue3 前端工程与构建产物（迁移期新旧并存：新页挂 /，旧页挂 /legacy）
WEB_DIR = APP_DIR / "web"
WEB_DIST_DIR = WEB_DIR / "dist"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8781
DEFAULT_OUTPUT_DIR = "bench_results"
MM_MAX_MEDIA_BYTES = 20 * 1024 * 1024
MM_INLINE_WARN_BYTES = 6 * 1024 * 1024
MM_MAX_INLINE_REQUEST_BYTES = 12 * 1024 * 1024
ROUTE_PROBE_COST_WARN_TOKENS = 100_000
ROUTE_PROBE_COST_MAX_TOKENS = 1_000_000
