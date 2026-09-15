"""平台全局配置：路径与默认值。"""
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = APP_DIR.parent

DATA_DIR = APP_DIR / "data"
DB_PATH = DATA_DIR / "llmeter.db"
STATIC_DIR = APP_DIR / "static"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_OUTPUT_DIR = "bench_results"
