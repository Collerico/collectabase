import errno
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from .version import APP_VERSION

# Load env before importing local modules that read os.getenv at import time.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")
load_dotenv(PROJECT_ROOT / "backend" / ".env")

from .api.routes.games import router as games_router
from .api.routes.backups import router as backups_router
from .api.routes.import_export import UPLOADS_DIR, router as import_export_router
from .api.routes.lookup import router as lookup_router
from .api.routes.lots import router as lots_router
from .api.routes.settings import router as settings_router
from .api.routes.stats import router as stats_router
from .clz_import import router as clz_router
from .database import init_db
from .price_tracker import router as price_router
from .scheduler import init_scheduler, shutdown_scheduler


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    init_scheduler()
    try:
        yield
    finally:
        shutdown_scheduler()


app = FastAPI(title="Collectabase", version=APP_VERSION, lifespan=lifespan)
_startup_time = time.time()
app.include_router(games_router)
app.include_router(backups_router)
app.include_router(lookup_router)
app.include_router(lots_router)
app.include_router(import_export_router)
app.include_router(stats_router)
app.include_router(settings_router)
app.include_router(clz_router)
app.include_router(price_router)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend", "dist")
FRONTEND_DIR_RESOLVED = Path(FRONTEND_DIR).resolve()
frontend_files = StaticFiles(directory=FRONTEND_DIR_RESOLVED, check_dir=False)
CONSOLE_FALLBACKS_DIR = os.path.join(BASE_DIR, "backend", "static", "console-fallbacks")

assets_dir = os.path.join(FRONTEND_DIR, "assets")
if os.path.isdir(assets_dir):
    app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

if os.path.isdir(CONSOLE_FALLBACKS_DIR):
    app.mount("/console-fallbacks", StaticFiles(directory=CONSOLE_FALLBACKS_DIR), name="console-fallbacks")

os.makedirs(UPLOADS_DIR, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=UPLOADS_DIR), name="uploads")


@app.get("/api/health")
async def health_check():
    """Health check for Docker / Portainer / monitoring."""
    from .database import get_db
    db_ok = False
    try:
        with get_db() as db:
            db.execute("SELECT 1").fetchone()
            db_ok = True
    except Exception:
        pass
    uptime = int(time.time() - _startup_time)
    return {
        "status": "healthy" if db_ok else "degraded",
        "version": APP_VERSION,
        "database": "ok" if db_ok else "error",
        "uptime_seconds": uptime,
    }


@app.get("/")
async def root():
    return FileResponse(str(FRONTEND_DIR_RESOLVED / "index.html"))


@app.get("/{path:path}")
async def catch_all(path: str, request: Request):
    if path.startswith("api/"):
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "API endpoint not found"})

    # Let StaticFiles enforce its canonical-path and symlink containment checks.
    # Only the constant SPA entry point is ever passed directly to FileResponse.
    if "\x00" not in path:
        try:
            return await frontend_files.get_response(path, request.scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404:
                raise
        except OSError as exc:
            if exc.errno != errno.ENAMETOOLONG:
                raise
    return FileResponse(str(FRONTEND_DIR_RESOLVED / "index.html"))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
