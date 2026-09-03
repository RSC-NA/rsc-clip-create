from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.config import get_settings
from backend.app.database import Base, engine
from backend.app.migrations import ensure_media_columns
from backend.app.routers import admin, auth, clips, jobs, movies, videos
from backend.app.storage import ensure_storage_dirs


settings = get_settings()
app = FastAPI(title=settings.app_name)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_public_url],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup() -> None:
    ensure_storage_dirs()
    Base.metadata.create_all(bind=engine)
    ensure_media_columns(engine)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/v1/health")
def versioned_health():
    return {"status": "ok"}


app.include_router(auth.router, prefix="/api/v1")
app.include_router(clips.router, prefix="/api/v1")
app.include_router(movies.router, prefix="/api/v1")
app.include_router(jobs.router, prefix="/api/v1")
app.include_router(videos.router, prefix="/api/v1")
app.include_router(admin.router, prefix="/api/v1")
