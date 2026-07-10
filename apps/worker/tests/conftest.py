import os

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://cockpit:test@localhost:5432/cockpit")
os.environ.setdefault("INTERNAL_API_TOKEN", "dev-token")
