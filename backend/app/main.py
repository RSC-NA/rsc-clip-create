from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.config import get_settings
from backend.app.database import Base, SessionLocal, engine
from backend.app.logging_config import configure_logging
from backend.app.migrations import ensure_media_columns, ensure_nullable_columns
from backend.app.routers import admin, auth, clips, config, jobs, logos, movies, videos
from backend.app.services.render_worker import requeue_stale_jobs
from backend.app.storage import ensure_storage_dirs


settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    ensure_storage_dirs()
    Base.metadata.create_all(bind=engine)
    ensure_media_columns(engine)
    ensure_nullable_columns(engine)
    with SessionLocal() as db:
        requeue_stale_jobs(db)
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_public_url],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/v1/health")
def versioned_health():
    return {"status": "ok"}


app.include_router(auth.router, prefix="/api/v1")
app.include_router(config.router, prefix="/api/v1")
app.include_router(clips.router, prefix="/api/v1")
app.include_router(logos.router, prefix="/api/v1")
app.include_router(movies.router, prefix="/api/v1")
app.include_router(jobs.router, prefix="/api/v1")
app.include_router(videos.router, prefix="/api/v1")
app.include_router(admin.router, prefix="/api/v1")
