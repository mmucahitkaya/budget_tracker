import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from . import prefs, scheduler, services, telegram
from .config import STATIC_DIR
from .db import Base, SessionLocal, engine, migrate
from .models import Document
from .routers import cards, documents, investments, misc, reports, savings, transactions
from .seed import seed

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("budget_tracker")
# httpx logs every request with its URL; disabled because the Telegram URL contains the bot token
logging.getLogger("httpx").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(engine)
    migrate()
    db = SessionLocal()
    try:
        seed(db)
        prefs.load(db)
        services.migrate_paid_statements(db)
        services.migrate_installments_monthly(db)
        services.realign_statement_installments(db)
        # Re-queue documents left half-processed during a restart
        stuck = db.scalars(select(Document).where(Document.status.in_(["pending", "processing"]))).all()
        for d in stuck:
            d.status = "pending"
        db.commit()
        for d in stuck:
            documents.enqueue(d.id)
    finally:
        db.close()
    # On startup, catch up on FX rates and missed recurring payments (separate thread so we don't block on the network)
    threading.Thread(target=scheduler.daily, daemon=True).start()
    sched = scheduler.start()
    telegram.start()
    log.info("Budget started")
    yield
    sched.shutdown(wait=False)


app = FastAPI(title="Budget", lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json")

@app.middleware("http")
async def limit_body_size(request, call_next):
    """Reject requests whose declared body size exceeds the limit without reading the body (60 MB upload limit + margin)."""
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > 70 * 1024 * 1024:
        return JSONResponse({"detail": "Request too large"}, status_code=413)
    return await call_next(request)


for r in (transactions.router, cards.router, misc.router, documents.router, reports.router, savings.router, investments.router):
    app.include_router(r)


@app.get("/api/health")
def health():
    from .config import DATA_DIR, settings

    # For diagnostics: configuration status without any secret values
    return {
        "ok": True,
        "options_file": (DATA_DIR / "options.json").exists(),
        "ollama_configured": bool(settings.ollama_url),
        "ollama_model": settings.ollama_model,
        "telegram_configured": bool(settings.telegram_bot_token),
        "notify_services": len(settings.notify_services),
    }


@app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
def api_not_found(path: str):
    return JSONResponse({"detail": "Not found"}, status_code=404)


if (STATIC_DIR / "index.html").exists():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        f = STATIC_DIR / path
        if path and f.is_file() and STATIC_DIR in f.resolve().parents:
            return FileResponse(f)
        return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})
