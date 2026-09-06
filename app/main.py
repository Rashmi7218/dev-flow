from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.db import init_db
from app.webhooks import github, jira, slack


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(title="DevFlow AI", lifespan=lifespan)

app.include_router(github.router)
app.include_router(jira.router)
app.include_router(slack.router)


@app.get("/health")
async def health():
    return {"status": "ok"}
