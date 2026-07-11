"""Theme tags: underlying_tags rows surface on portfolio positions and
exposure rows; options inherit the underlying's tags; untagged -> []."""
import json
import os

import pytest
from sqlalchemy import create_engine, text

from test_portfolio_api import _cleanup, _seed, STK, CALL  # same-dir import (pytest rootdir path)
from fastapi.testclient import TestClient
from app.main import app


@pytest.fixture
def client_with_tags():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("needs postgres (TEST_DATABASE_URL)")
    eng = create_engine(url)
    _cleanup(eng)
    _seed(eng)
    with eng.begin() as conn:
        conn.execute(text(
            "INSERT INTO underlying_tags (underlying, tags) "
            "VALUES (:u, CAST(:t AS jsonb)) "
            "ON CONFLICT (underlying) DO UPDATE SET tags = EXCLUDED.tags"),
            {"u": STK, "t": json.dumps(["ai", "cpo-optics"])})
    import app.main as main
    prev = main._engine
    main._engine = eng
    yield TestClient(app)
    main._engine = prev
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM underlying_tags WHERE underlying = :u"), {"u": STK})
    _cleanup(eng)
    eng.dispose()


HEADERS = {"X-Internal-Token": "dev-token"}


def test_portfolio_positions_carry_tags_and_options_inherit(client_with_tags):
    body = client_with_tags.get("/internal/portfolio", headers=HEADERS).json()
    by_symbol = {p["symbol"]: p for p in body["positions"]}
    assert by_symbol[STK]["tags"] == ["ai", "cpo-optics"]
    assert by_symbol[CALL]["tags"] == ["ai", "cpo-optics"]  # option inherits


def test_exposure_rows_carry_tags_untagged_empty(client_with_tags):
    rows = client_with_tags.get("/internal/exposure", headers=HEADERS).json()
    tagged = [r for r in rows if r["underlying"] == STK]
    assert tagged and tagged[0]["tags"] == ["ai", "cpo-optics"]
    for r in rows:
        assert isinstance(r["tags"], list)  # every row has the key
