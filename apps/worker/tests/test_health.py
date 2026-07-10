from fastapi.testclient import TestClient
from app.main import app

def test_health_reports_db(monkeypatch):
    from app import main
    monkeypatch.setattr(main, "check_db", lambda: "ok")
    client = TestClient(app)
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["db"] == "ok"
