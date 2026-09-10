async def test_dashboard_requires_auth(client):
    resp = await client.get("/dashboard")
    assert resp.status_code == 401


async def test_events_api_requires_auth(client):
    resp = await client.get("/api/events")
    assert resp.status_code == 401


async def test_dashboard_rejects_wrong_credentials(client):
    client.auth = ("admin", "wrong-password")
    resp = await client.get("/dashboard")
    assert resp.status_code == 401


async def test_dashboard_accepts_correct_credentials(admin_client):
    resp = await admin_client.get("/dashboard")
    assert resp.status_code == 200
