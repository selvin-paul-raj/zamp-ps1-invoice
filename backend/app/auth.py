"""API keys, roles (RBAC) and rate limiting. Off by default (AUTH_MODE=off) so the demo needs no setup.

AUTH_MODE=apikey and API_KEYS="key:role[:name],key2:role2[:name2]". Clients send `X-API-Key: <key>`
(or `Authorization: Bearer <key>`). Roles, each including the ones before it:
    viewer    read runs, evidence, stats
    operator  ingest / upload / re-run / demo seed
    reviewer  resolve a hold with REJECT or keep it on hold
    approver  release a held invoice for payment (APPROVE)  <- the money action
    admin     requeue dead-lettered jobs, reload master data
Keys are compared in constant time and never logged; the reviewer name on the audit trail comes from the key.
RATE_LIMIT_PER_MIN > 0 adds a per-client sliding-window limit (in memory; use a shared store with several replicas).
"""
from __future__ import annotations

import hmac
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Optional

ROLES = ["viewer", "operator", "reviewer", "approver", "admin"]


@dataclass(frozen=True)
class Principal:
    name: str
    role: str

    def has(self, role: str) -> bool:
        return ROLES.index(self.role) >= ROLES.index(role)


ANON_ADMIN = Principal("demo-user", "admin")  # AUTH_MODE=off: everything allowed, as in the original demo


def parse_keys(spec: str) -> list[tuple[str, Principal]]:
    out = []
    for i, item in enumerate(x.strip() for x in (spec or "").split(",") if x.strip()):
        parts = item.split(":")
        if len(parts) < 2 or parts[1] not in ROLES:
            raise ValueError(f"API_KEYS entry {i + 1} must be key:role[:name] with role in {ROLES}")
        out.append((parts[0], Principal(parts[2] if len(parts) > 2 else f"{parts[1]}-{i + 1}", parts[1])))
    return out


def authenticate(mode: str, keys: list[tuple[str, Principal]], presented: Optional[str]) -> Optional[Principal]:
    if mode != "apikey":
        return ANON_ADMIN
    if not presented:
        return None
    found = None
    for k, p in keys:
        if hmac.compare_digest(k.encode(), presented.encode()):
            found = p
    return found


class RateLimiter:
    def __init__(self, per_min: int):
        self.per_min = per_min
        self.hits: dict[str, deque] = defaultdict(deque)
        self.lock = threading.Lock()

    def allow(self, client: str) -> tuple[bool, int]:
        if self.per_min <= 0:
            return True, 0
        now = time.monotonic()
        with self.lock:
            q = self.hits[client]
            while q and now - q[0] > 60:
                q.popleft()
            if len(q) >= self.per_min:
                return False, int(60 - (now - q[0])) + 1
            q.append(now)
            return True, 0
