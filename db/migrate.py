"""Apply db/schema.sql and db/seed.sql idempotently.

Usage:  python db/migrate.py
(Also available on the deployed app as POST /api/admin/migrate with the CRON_SECRET.)
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def migrate() -> dict:
    from app import db

    schema = (ROOT / "db" / "schema.sql").read_text(encoding="utf-8")
    seed = (ROOT / "db" / "seed.sql").read_text(encoding="utf-8")
    db.run_script(schema)
    db.run_script(seed)
    tables = db.query(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = current_schema() ORDER BY 1"
    )
    return {"tables": [t["table_name"] for t in tables]}


if __name__ == "__main__":
    result = migrate()
    print(f"Schema applied. {len(result['tables'])} tables: {', '.join(result['tables'])}")
