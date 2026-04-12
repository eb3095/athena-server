"""FastAPI application - main entry point."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from athena.core.openai import init_openai
from athena.core.redis import close_redis, init_redis
from athena.jobs.background import recover_stale_jobs, start_background_tasks
from athena.routes import router


background_tasks_handles: list = []


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan - initialize and cleanup resources."""
    global background_tasks_handles

    # Initialize clients
    init_openai()
    await init_redis()

    # Recover any jobs that were processing when server last shutdown
    await recover_stale_jobs()

    # Start background tasks
    background_tasks_handles = await start_background_tasks()

    yield

    # Cleanup
    await close_redis()


app = FastAPI(lifespan=lifespan)
app.include_router(router)
