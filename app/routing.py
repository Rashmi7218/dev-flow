import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import ChannelBinding, RepoConfig

logger = logging.getLogger(__name__)


async def channels_for_repo(db: AsyncSession, repo: str) -> list[str]:
    result = await db.execute(
        select(ChannelBinding.slack_channel).where(ChannelBinding.repo == repo)
    )
    channels = list(result.scalars().all())
    return channels or [settings.slack_default_channel]


async def jira_project_for_repo(db: AsyncSession, repo: str) -> str:
    result = await db.execute(
        select(RepoConfig.jira_project_key).where(RepoConfig.repo == repo)
    )
    project_key = result.scalar_one_or_none()
    return project_key or settings.jira_project_key


async def repos_for_channel(db: AsyncSession, channel: str) -> list[str]:
    result = await db.execute(
        select(ChannelBinding.repo).where(ChannelBinding.slack_channel == channel)
    )
    return list(result.scalars().all())


async def jira_project_for_channel(db: AsyncSession, channel: str) -> str:
    repos = await repos_for_channel(db, channel)
    if not repos:
        return settings.jira_project_key
    if len(repos) > 1:
        logger.warning(
            "Channel %s is bound to multiple repos (%s); using %s for Jira project lookup",
            channel,
            repos,
            repos[0],
        )
    return await jira_project_for_repo(db, repos[0])


async def channels_for_jira_project(db: AsyncSession, project_key: str) -> list[str]:
    result = await db.execute(
        select(RepoConfig.repo).where(RepoConfig.jira_project_key == project_key)
    )
    repos = list(result.scalars().all())
    if not repos:
        return [settings.slack_default_channel]

    channels: set[str] = set()
    for repo in repos:
        channels.update(await channels_for_repo(db, repo))
    return sorted(channels) or [settings.slack_default_channel]
