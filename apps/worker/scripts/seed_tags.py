"""Idempotent theme-tag seed for underlying_tags.

Usage (in the worker container or locally with DATABASE_URL set):
    uv run python scripts/seed_tags.py

Upserts the mapping below — safe to re-run; only listed underlyings are
touched, so owner edits to other rows survive. Tags are themes, not advice:
options inherit their underlying's tags automatically in the API layer.
"""
import json

from sqlalchemy import create_engine, text

from app.config import settings

TAGS: dict[str, list[str]] = {
    "SLS": ["biotech"],
    "MU": ["ai", "semis", "memory"],
    "AMD": ["ai", "semis"],
    "NVDA": ["ai", "semis"],
    "TSM": ["ai", "semis"],
    "TSEM": ["ai", "semis"],
    "MRVL": ["ai", "semis"],
    "TXN": ["semis"],
    "ON": ["semis", "power"],
    "DRAM": ["semis", "memory"],
    "TRT": ["semis", "semicap"],
    "AMAT": ["ai", "semis", "semicap"],
    "BESIY": ["ai", "semis", "semicap"],
    "FORM": ["ai", "semis", "semicap"],
    "MKSI": ["ai", "semis", "semicap"],
    "VECO": ["semis", "semicap"],
    "LITE": ["ai", "cpo-optics"],
    "COHR": ["ai", "cpo-optics"],
    "AAOI": ["ai", "cpo-optics"],
    "AXTI": ["cpo-optics", "semis"],
    "LASR": ["cpo-optics", "defense"],
    "NBIS": ["ai", "neocloud", "data-center"],
    "CRWV": ["ai", "neocloud", "data-center"],
    "HUT": ["ai", "data-center", "crypto"],
    "HIVE": ["ai", "data-center", "crypto"],
    "CIFR": ["ai", "data-center", "crypto"],
    "IREN": ["ai", "data-center", "crypto"],
    "WULF": ["ai", "data-center", "crypto"],
    "GLXY": ["crypto", "data-center"],
    "CLSK": ["crypto"],
    "VICR": ["ai", "power"],
    "MPWR": ["ai", "power", "semis"],
    "BE": ["ai", "power"],
    "TE": ["power"],
    "TGEN": ["power"],
    "FLNC": ["power", "storage"],
    "SMR": ["ai", "nuclear", "power"],
    "UUUU": ["nuclear", "rare-earth", "materials"],
    "USAR": ["rare-earth", "materials"],
    "MP": ["rare-earth", "materials"],
    "METC": ["materials", "coal"],
    "AVAV": ["defense", "drones"],
    "OUST": ["lidar", "robotics"],
    "SKM": ["telco", "ai"],
    "MELI": ["ecommerce", "latam"],
    "HIMS": ["healthcare"],
    "GOOGL": ["ai", "big-tech"],
    "MSFT": ["ai", "big-tech"],
    "RDDT": ["ai", "social"],
    "NUAI": ["ai"],
    "BRK.B": ["diversified"],
    "VOO": ["index"],
    "GLD": ["gold"],
    "BRUN": [],
    "KEEL": [],
}


def main() -> None:
    engine = create_engine(settings.database_url)
    with engine.begin() as conn:
        for underlying, tags in TAGS.items():
            conn.execute(text(
                "INSERT INTO underlying_tags (underlying, tags) "
                "VALUES (:u, CAST(:t AS jsonb)) "
                "ON CONFLICT (underlying) DO UPDATE SET tags = EXCLUDED.tags"),
                {"u": underlying, "t": json.dumps(tags)})
    print(f"seeded {len(TAGS)} underlyings")


if __name__ == "__main__":
    main()
