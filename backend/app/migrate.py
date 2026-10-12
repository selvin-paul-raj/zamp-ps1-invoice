"""Run Alembic migrations programmatically (DB_MIGRATE=alembic). Safe to call on every start: upgrade to head is idempotent."""
from __future__ import annotations

from pathlib import Path


def upgrade_head(url: str) -> None:
    from alembic import command
    from alembic.config import Config

    root = Path(__file__).resolve().parent.parent
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    cfg.attributes["url"] = url
    command.upgrade(cfg, "head")
