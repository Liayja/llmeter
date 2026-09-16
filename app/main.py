"""平台入口：python -m app.main [--port 8765]"""
import argparse

import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .db import init_db
from .routers import connections, models, parity, prompts, results, scenarios, tasks
from .services import key_store
from .settings import DEFAULT_HOST, DEFAULT_PORT, STATIC_DIR

app = FastAPI(title="llmeter 压测平台", version="0.1.0")

app.include_router(connections.router)
app.include_router(models.router)
app.include_router(prompts.router)
app.include_router(scenarios.router)
app.include_router(parity.router)
app.include_router(tasks.router)
app.include_router(results.router)


@app.get("/api/health")
def health() -> JSONResponse:
    warning = key_store.fallback_warning()
    return JSONResponse({"ok": True, "warning": warning})


@app.on_event("startup")
def _startup() -> None:
    init_db()


# 静态前端（放在最后 mount，避免遮蔽 /api）
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")


def main() -> None:
    parser = argparse.ArgumentParser(description="llmeter 本地 Web 压测平台")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()
    init_db()
    print(f"🌐 llmeter 平台启动中: http://{args.host}:{args.port}")
    uvicorn.run("app.main:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
