"""Import Node inventory from seeds/nodes.json into the database.

Matches on `host` — updates existing nodes, inserts new ones.
Does NOT delete nodes that are in the DB but not in the seed file.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models import Node

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    print("ERROR: Set DATABASE_URL environment variable.")
    sys.exit(1)

engine = create_engine(DATABASE_URL)

seeds_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "seeds", "nodes.json")
if not os.path.exists(seeds_path):
    print(f"ERROR: Seed file not found: {seeds_path}")
    sys.exit(1)

with open(seeds_path) as f:
    seed_rows = json.load(f)

IMPORT_FIELDS = [
    "name", "node_id", "host", "web_port", "ssh_port", "location",
    "include_in_topology", "topology_level", "topology_unit",
    "enabled", "notes", "api_username", "api_password", "api_use_https",
    "ping_enabled", "ping_interval_seconds",
]

inserted = 0
updated = 0

with Session(engine) as db:
    for row in seed_rows:
        host = row.get("host", "").strip()
        if not host:
            print(f"  SKIP: row with no host: {row.get('name')}")
            continue

        existing = db.scalars(select(Node).where(Node.host == host)).first()
        if existing:
            for field in IMPORT_FIELDS:
                if field in row:
                    setattr(existing, field, row[field])
            updated += 1
        else:
            node = Node(**{k: row[k] for k in IMPORT_FIELDS if k in row})
            db.add(node)
            inserted += 1

    db.commit()

print(f"Seed complete: {inserted} inserted, {updated} updated ({len(seed_rows)} total in seed file)")
