import os

# Set an async-compatible DATABASE_URL before any app module is imported.
# app/db.py creates the async engine at module level, so this must run
# before any test transitively imports from app.db.
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
