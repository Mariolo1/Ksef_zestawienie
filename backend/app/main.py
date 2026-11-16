import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware

from . import config, worker
from .auth_routes import router as auth_router
from .company_routes import router as company_router
from .db import Base, engine
from .report_routes import router as report_router

logging.basicConfig(level=logging.INFO)


def _add_missing_columns():
    """Dokłada nowe kolumny do istniejących tabel (do czasu wprowadzenia Alembic)."""
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    blob = "BYTEA" if engine.dialect.name == "postgresql" else "BLOB"
    wanted = {"companies": {"auth_method": "VARCHAR(10) NOT NULL DEFAULT 'cert'",
                            "token_enc": f"{blob} NULL"}}
    with engine.begin() as conn:
        for table, cols in wanted.items():
            have = {c["name"] for c in insp.get_columns(table)}
            for name, ddl in cols.items():
                if name not in have:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(engine)  # wersja testowa; docelowo migracje Alembic
    _add_missing_columns()
    worker.start()
    yield


app = FastAPI(title="KSeF - zestawienia dla księgowej", lifespan=lifespan,
              docs_url="/api/docs", openapi_url="/api/openapi.json")
# sesja Starlette potrzebna tylko do stanu OAuth (Google)
app.add_middleware(SessionMiddleware, secret_key=config.SECRET_KEY, same_site="lax",
                   https_only=config.COOKIE_SECURE, max_age=600)

app.include_router(auth_router)
app.include_router(company_router)
app.include_router(report_router)


@app.get("/api/health")
def health():
    return {"ok": True}
