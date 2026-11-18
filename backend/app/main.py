from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import auth, conversations, customers
from app.db.base import Base
from app.db.session import engine
import app.models  # noqa: F401  (register tables)


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Week 1 shortcut. TODO Week 2: replace with Alembic migrations.
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield


app = FastAPI(title="SupportPilot API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(auth.router, prefix="/api/v1")
app.include_router(customers.router, prefix="/api/v1")
app.include_router(conversations.router, prefix="/api/v1")


@app.get("/health")
async def health():
    return {"status": "ok"}
