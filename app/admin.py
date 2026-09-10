from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_admin
from app.db import get_db
from app.models import ChannelBinding, RepoConfig

router = APIRouter(
    prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)]
)


class RepoConfigIn(BaseModel):
    repo: str
    jira_project_key: str


class ChannelBindingIn(BaseModel):
    repo: str
    slack_channel: str


def _repo_config_dict(row: RepoConfig) -> dict:
    return {"id": row.id, "repo": row.repo, "jira_project_key": row.jira_project_key}


def _channel_binding_dict(row: ChannelBinding) -> dict:
    return {"id": row.id, "repo": row.repo, "slack_channel": row.slack_channel}


@router.get("/repos")
async def list_repo_configs(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(RepoConfig).order_by(RepoConfig.repo))
    return [_repo_config_dict(row) for row in result.scalars().all()]


@router.post("/repos")
async def upsert_repo_config(body: RepoConfigIn, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(RepoConfig).where(RepoConfig.repo == body.repo))
    existing = result.scalar_one_or_none()
    if existing:
        existing.jira_project_key = body.jira_project_key
        row = existing
    else:
        row = RepoConfig(repo=body.repo, jira_project_key=body.jira_project_key)
        db.add(row)
    await db.commit()
    await db.refresh(row)
    return _repo_config_dict(row)


@router.delete("/repos/{repo_id}")
async def delete_repo_config(repo_id: int, db: AsyncSession = Depends(get_db)):
    row = await db.get(RepoConfig, repo_id)
    if not row:
        raise HTTPException(status_code=404, detail="Repo config not found")
    await db.delete(row)
    await db.commit()
    return {"status": "deleted"}


@router.get("/bindings")
async def list_channel_bindings(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ChannelBinding).order_by(ChannelBinding.repo))
    return [_channel_binding_dict(row) for row in result.scalars().all()]


@router.post("/bindings")
async def create_channel_binding(body: ChannelBindingIn, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(ChannelBinding).where(
            ChannelBinding.repo == body.repo,
            ChannelBinding.slack_channel == body.slack_channel,
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        return _channel_binding_dict(existing)

    row = ChannelBinding(repo=body.repo, slack_channel=body.slack_channel)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return _channel_binding_dict(row)


@router.delete("/bindings/{binding_id}")
async def delete_channel_binding(binding_id: int, db: AsyncSession = Depends(get_db)):
    row = await db.get(ChannelBinding, binding_id)
    if not row:
        raise HTTPException(status_code=404, detail="Channel binding not found")
    await db.delete(row)
    await db.commit()
    return {"status": "deleted"}
