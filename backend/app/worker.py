"""Standalone worker: `python -m app.worker` (WORKER_MODE=external on the API so only this process runs jobs).

Pulls from the same database-backed queue as the in-process worker. Run several copies against Postgres to scale out;
SELECT ... FOR UPDATE SKIP LOCKED keeps them from taking the same job, and leases recover jobs from a crashed worker.
"""
from __future__ import annotations

import logging
import signal
import time

from .config import settings
from .db import make_engine
from .service import Service
from .telemetry import setup

log = logging.getLogger("zamp_ps1.worker")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    setup("zamp-ps1-worker")
    svc = Service(make_engine(settings.database_url), settings)
    stop = {"flag": False}
    signal.signal(signal.SIGTERM, lambda *_: stop.update(flag=True))
    signal.signal(signal.SIGINT, lambda *_: stop.update(flag=True))
    log.info("worker %s started (jev=%s)", svc.worker_id, settings.jev_mode)
    while not stop["flag"]:
        job = svc.claim_job()
        if job is None:
            time.sleep(0.3)
            continue
        svc.run_job(*job)
    log.info("worker %s stopped", svc.worker_id)


if __name__ == "__main__":
    main()
