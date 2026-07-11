import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

pg = pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="needs postgres")


def test_journal_requires_token():
    from app.main import app
    c = TestClient(app)
    assert c.get("/internal/journal").status_code == 401
    assert c.post("/internal/journal", json={}).status_code == 401
    assert c.request("DELETE", "/internal/journal/1").status_code == 401


@pytest.fixture()
def client():
    import app.main as main
    engine = create_engine(os.environ["TEST_DATABASE_URL"])

    def _cleanup():
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM journal_entries WHERE actor = 'jrnl-test'"))
            conn.execute(text("DELETE FROM audit_log WHERE actor = 'jrnl-test'"))

    _cleanup()
    prev = main._engine
    main._engine = engine
    try:
        yield TestClient(main.app)
    finally:
        main._engine = prev
        _cleanup()

H = {"X-Internal-Token": "dev-token"}


def _post(client, **over):
    body = {"symbol": "JRNLQ", "tag": "thesis", "note": "gamma squeeze setup into earnings",
            "actor": "jrnl-test"}
    body.update(over)
    return client.post("/internal/journal", json=body, headers=H)


@pg
def test_create_and_list_by_symbol(client):
    r = _post(client, target_usd="25.50", stop_usd="12.00", confidence=4)
    assert r.status_code == 200, r.text
    e = r.json()
    assert e["symbol"] == "JRNLQ" and e["tag"] == "thesis"
    assert e["target_usd"] == "25.5000" and e["confidence"] == 4
    _post(client, symbol="JRNLZ", note="unrelated other note")
    rows = client.get("/internal/journal?symbol=JRNLQ", headers=H).json()
    assert [x["symbol"] for x in rows] == ["JRNLQ"]


@pg
def test_full_text_search_and_tag_filter(client):
    _post(client, note="premium harvesting on elevated implied volatility")
    _post(client, tag="trim", note="taking profits after the run")
    hits = client.get("/internal/journal?q=volatility", headers=H).json()
    assert len(hits) == 1 and "implied volatility" in hits[0]["note"]
    assert client.get("/internal/journal?q=zzzqqqxyzzy", headers=H).json() == []
    tagged = client.get("/internal/journal?tag=trim", headers=H).json()
    assert len(tagged) == 1 and tagged[0]["tag"] == "trim"


@pg
def test_ordering_newest_first_and_validation(client):
    _post(client, note="first entry")
    _post(client, note="second entry")
    rows = client.get("/internal/journal?symbol=JRNLQ", headers=H).json()
    assert rows[0]["note"] == "second entry"
    assert _post(client, confidence=9).status_code == 422
    assert _post(client, note="   ").status_code == 400
    assert _post(client, tag="").status_code == 400


@pg
def test_delete(client):
    eid = _post(client).json()["id"]
    assert client.request("DELETE", f"/internal/journal/{eid}", headers=H).status_code == 200
    assert client.get("/internal/journal?symbol=JRNLQ", headers=H).json() == []
    assert client.request("DELETE", f"/internal/journal/{eid}", headers=H).status_code == 404
