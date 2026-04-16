"""Export Node inventory to seeds/nodes.json for cross-environment seeding."""
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

EXPORT_FIELDS = [
    "name", "node_id", "host", "web_port", "ssh_port", "location",
    "topology_map_id",
    "enabled", "notes", "api_username", "api_password", "api_use_https",
    "ping_enabled", "ping_interval_seconds",
]

rows = []
with Session(engine) as db:
    nodes = db.scalars(select(Node).order_by(Node.name)).all()
    for node in nodes:
        row = {}
        for field in EXPORT_FIELDS:
            row[field] = getattr(node, field)
        rows.append(row)

seeds_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "seeds")
os.makedirs(seeds_dir, exist_ok=True)
out_path = os.path.join(seeds_dir, "nodes.json")

with open(out_path, "w") as f:
    json.dump(rows, f, indent=2, default=str)

print(f"Exported {len(rows)} nodes to {out_path}")
