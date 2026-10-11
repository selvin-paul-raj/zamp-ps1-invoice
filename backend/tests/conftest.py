import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["PIPELINE_STEP_DELAY_MS"] = "0"
os.environ["WORKER_ENABLED"] = "0"

from app.config import Settings  # noqa: E402
from app.db import make_engine  # noqa: E402
from app.service import Service  # noqa: E402

SEED = ROOT / "seed"


@pytest.fixture
def svc(tmp_path):
    s = Service(make_engine(f"sqlite:///{tmp_path}/t.db"), Settings())
    s.load_master(SEED)
    return s


@pytest.fixture
def seed_invoices():
    items = json.loads((SEED / "invoices.json").read_text())
    return {i["id"]: i for i in items}


def run_one(svc, payload, key=None):
    res = svc.ingest(payload, idempotency_key=key)
    svc.drain(delay_ms=0)
    return svc.get_run(res["run_id"])


def run_in_order(svc, items, ids):
    """Process seed invoices in received order so earlier ones become history for later ones."""
    out = {}
    for i in sorted(ids, key=lambda x: items[x]["received_at"]):
        out[i] = run_one(svc, items[i], key=f"seed:{i}")
    return out
