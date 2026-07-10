import os
import pytest
from sqlalchemy import create_engine, inspect

pytestmark = pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="needs postgres")

EXPECTED = {"users", "broker_accounts", "instruments", "positions",
            "snapshots", "cash_flows", "audit_log"}

def test_migration_creates_all_tables():
    eng = create_engine(os.environ["TEST_DATABASE_URL"])
    assert EXPECTED <= set(inspect(eng).get_table_names())
