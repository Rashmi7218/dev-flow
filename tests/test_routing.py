from app.config import settings
from app.db import SessionLocal
from app.models import ChannelBinding, RepoConfig
from app.routing import (
    channels_for_jira_project,
    channels_for_repo,
    jira_project_for_channel,
    jira_project_for_repo,
    repos_for_channel,
)


async def test_channels_for_repo_falls_back_to_default_when_unbound():
    async with SessionLocal() as db:
        channels = await channels_for_repo(db, "acme/unbound")
    assert channels == [settings.slack_default_channel]


async def test_channels_for_repo_returns_all_bound_channels():
    async with SessionLocal() as db:
        db.add_all(
            [
                ChannelBinding(repo="acme/widgets", slack_channel="C1"),
                ChannelBinding(repo="acme/widgets", slack_channel="C2"),
                ChannelBinding(repo="acme/other", slack_channel="C3"),
            ]
        )
        await db.commit()
        channels = await channels_for_repo(db, "acme/widgets")
    assert sorted(channels) == ["C1", "C2"]


async def test_jira_project_for_repo_falls_back_to_default_when_unbound():
    async with SessionLocal() as db:
        project = await jira_project_for_repo(db, "acme/unbound")
    assert project == settings.jira_project_key


async def test_jira_project_for_repo_returns_configured_project():
    async with SessionLocal() as db:
        db.add(RepoConfig(repo="acme/widgets", jira_project_key="WID"))
        await db.commit()
        project = await jira_project_for_repo(db, "acme/widgets")
    assert project == "WID"


async def test_repos_for_channel_returns_bound_repos():
    async with SessionLocal() as db:
        db.add_all(
            [
                ChannelBinding(repo="acme/widgets", slack_channel="C1"),
                ChannelBinding(repo="acme/gadgets", slack_channel="C1"),
            ]
        )
        await db.commit()
        repos = await repos_for_channel(db, "C1")
    assert sorted(repos) == ["acme/gadgets", "acme/widgets"]


async def test_jira_project_for_channel_falls_back_when_unbound():
    async with SessionLocal() as db:
        project = await jira_project_for_channel(db, "C-unbound")
    assert project == settings.jira_project_key


async def test_jira_project_for_channel_uses_first_repo_when_ambiguous():
    async with SessionLocal() as db:
        db.add_all(
            [
                RepoConfig(repo="acme/widgets", jira_project_key="WID"),
                RepoConfig(repo="acme/gadgets", jira_project_key="GAD"),
                ChannelBinding(repo="acme/widgets", slack_channel="C1"),
                ChannelBinding(repo="acme/gadgets", slack_channel="C1"),
            ]
        )
        await db.commit()
        project = await jira_project_for_channel(db, "C1")
    assert project in ("WID", "GAD")


async def test_channels_for_jira_project_unions_channels_across_repos():
    async with SessionLocal() as db:
        db.add_all(
            [
                RepoConfig(repo="acme/widgets", jira_project_key="WID"),
                RepoConfig(repo="acme/gadgets", jira_project_key="WID"),
                ChannelBinding(repo="acme/widgets", slack_channel="C1"),
                ChannelBinding(repo="acme/gadgets", slack_channel="C2"),
            ]
        )
        await db.commit()
        channels = await channels_for_jira_project(db, "WID")
    assert sorted(channels) == ["C1", "C2"]


async def test_channels_for_jira_project_falls_back_when_unbound():
    async with SessionLocal() as db:
        channels = await channels_for_jira_project(db, "NOPE")
    assert channels == [settings.slack_default_channel]
