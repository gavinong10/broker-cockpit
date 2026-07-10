from fastapi.testclient import TestClient
from app.main import app

def test_internal_requires_token():
    c = TestClient(app)
    assert c.get("/internal/ping").status_code == 401
    ok = c.get("/internal/ping", headers={"X-Internal-Token": "dev-token"})
    assert ok.status_code == 200 and ok.json() == {"pong": True}
