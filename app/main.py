"""平台入口：python -m app.main [--port 8781]"""
import argparse

import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .db import init_db
from .routers import connections, models, parity, prompts, results, scenarios, tasks
from .services.parity import runner as parity_runner
from .services import key_store
from .settings import DEFAULT_HOST, DEFAULT_PORT, STATIC_DIR, WEB_DIST_DIR

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
    parity_runner.recover_stale_tasks()


# 静态前端（放在最后 mount，避免遮蔽 /api）
# 迁移期策略：Vue3 构建产物存在时挂到 /，旧静态页保留在 /legacy 以便随时回滚；
# 没有构建产物（未执行 npm run build）时，自动回退到旧页，避免平台打不开。
if WEB_DIST_DIR.exists():
    @app.get("/legacy", include_in_schema=False)
    def legacy_redirect() -> RedirectResponse:
        """兼容无尾斜杠访问，避免回滚入口 404。"""
        return RedirectResponse("/legacy/")

    app.mount("/legacy", StaticFiles(directory=str(STATIC_DIR), html=True), name="legacy")
    app.mount("/", StaticFiles(directory=str(WEB_DIST_DIR), html=True), name="web")
else:
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")


def main() -> None:
    parser = argparse.ArgumentParser(description="llmeter 本地 Web 压测平台")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()
    init_db()
    print(f"[llmeter] platform starting: http://{args.host}:{args.port}")
    if WEB_DIST_DIR.exists():
        print("   - Vue3 frontend enabled; legacy UI: /legacy")
    else:
        print("   - app/web/dist not found; legacy UI enabled")
    uvicorn.run("app.main:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
