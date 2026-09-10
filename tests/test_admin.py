async def test_admin_endpoints_require_auth(client):
    resp = await client.get("/api/admin/repos")
    assert resp.status_code == 401

    resp = await client.get("/api/admin/bindings")
    assert resp.status_code == 401


async def test_repo_config_crud(admin_client):
    resp = await admin_client.get("/api/admin/repos")
    assert resp.json() == []

    resp = await admin_client.post(
        "/api/admin/repos", json={"repo": "acme/widgets", "jira_project_key": "WID"}
    )
    assert resp.status_code == 200
    created = resp.json()
    assert created["repo"] == "acme/widgets"
    assert created["jira_project_key"] == "WID"

    resp = await admin_client.post(
        "/api/admin/repos", json={"repo": "acme/widgets", "jira_project_key": "WID2"}
    )
    assert resp.status_code == 200
    updated = resp.json()
    assert updated["id"] == created["id"]
    assert updated["jira_project_key"] == "WID2"

    resp = await admin_client.get("/api/admin/repos")
    assert len(resp.json()) == 1

    resp = await admin_client.delete(f"/api/admin/repos/{created['id']}")
    assert resp.status_code == 200

    resp = await admin_client.get("/api/admin/repos")
    assert resp.json() == []


async def test_delete_missing_repo_config_returns_404(admin_client):
    resp = await admin_client.delete("/api/admin/repos/9999")
    assert resp.status_code == 404


async def test_channel_binding_crud(admin_client):
    resp = await admin_client.post(
        "/api/admin/bindings", json={"repo": "acme/widgets", "slack_channel": "C123"}
    )
    assert resp.status_code == 200
    created = resp.json()
    assert created["repo"] == "acme/widgets"
    assert created["slack_channel"] == "C123"

    # Re-adding the same binding is idempotent, not a duplicate row.
    resp = await admin_client.post(
        "/api/admin/bindings", json={"repo": "acme/widgets", "slack_channel": "C123"}
    )
    assert resp.status_code == 200
    resp = await admin_client.get("/api/admin/bindings")
    assert len(resp.json()) == 1

    resp = await admin_client.delete(f"/api/admin/bindings/{created['id']}")
    assert resp.status_code == 200

    resp = await admin_client.get("/api/admin/bindings")
    assert resp.json() == []


async def test_delete_missing_channel_binding_returns_404(admin_client):
    resp = await admin_client.delete("/api/admin/bindings/9999")
    assert resp.status_code == 404
