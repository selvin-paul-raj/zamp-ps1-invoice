"""Runtime configuration. Everything is overridable with environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from decimal import Decimal


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _database_url() -> str:
    """DATABASE_URL wins; else build a Postgres URL from PG* vars (used by docker-compose); else SQLite."""
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    if os.environ.get("PGHOST"):
        from sqlalchemy.engine import URL

        return URL.create(
            "postgresql+psycopg",
            username=os.environ.get("PGUSER", "postgres"),
            password=os.environ.get("PG" + "PASSWORD") or None,
            host=os.environ["PGHOST"],
            port=int(os.environ.get("PGPORT", "5432")),
            database=os.environ.get("PGDATABASE", "postgres"),
        ).render_as_string(hide_password=False)
    return "sqlite:///./zamp_ps1.db"


def jev_api_key() -> str:
    """Read at call time, never stored on settings (so it is never logged or returned by an endpoint)."""
    return os.environ.get("JEV_API_KEY") or os.environ.get("TYPESAFE_API_KEY") or ""


def _jev_mode() -> str:
    mode = os.environ.get("JEV_MODE", "").strip().lower()
    en = os.environ.get("JEV_ENABLED", "").strip()
    if en == "0":
        return "off"
    if en == "1" and not mode:
        mode = "live"
    mode = mode or "replay"
    if mode == "live" and not jev_api_key():
        return "replay"
    return mode if mode in ("off", "replay", "live") else "replay"


@dataclass(frozen=True)
class Tolerances:
    """Matching tolerances. In production these would be per vendor / category."""

    # Unit price may exceed the PO price by at most this fraction (2%).
    price_pct: Decimal = Decimal("0.02")
    # ... and by at most this absolute amount per line (PO currency).
    price_abs_per_line: Decimal = Decimal("5000")
    # Cumulative billed amount on a PO may exceed the PO value by at most 1%.
    po_total_pct: Decimal = Decimal("0.01")
    # Rounding slack for arithmetic checks, per line.
    rounding_per_line: Decimal = Decimal("0.01")
    # Tax amount slack (absolute, invoice currency).
    tax_abs: Decimal = Decimal("1.00")
    # Possible-duplicate window.
    dup_amount_pct: Decimal = Decimal("0.005")
    dup_days: int = 60


@dataclass(frozen=True)
class Settings:
    database_url: str = field(default_factory=_database_url)
    # Artificial delay between pipeline steps so the live run view is watchable. 0 in tests.
    step_delay_ms: int = field(default_factory=lambda: int(_env("PIPELINE_STEP_DELAY_MS", "350")))
    worker_enabled: bool = field(default_factory=lambda: _env("WORKER_ENABLED", "1") == "1")
    max_attempts: int = field(default_factory=lambda: int(_env("WORKER_MAX_ATTEMPTS", "3")))
    approve_threshold: float = field(default_factory=lambda: float(_env("APPROVE_CONFIDENCE_THRESHOLD", "0.85")))
    # Optional LLM adapter for explanations. Unset = deterministic template explanation.
    llm_provider: str = field(default_factory=lambda: _env("DECISION_LLM_PROVIDER", ""))
    cors_origins: str = field(default_factory=lambda: _env("CORS_ORIGINS", "*"))
    # Buyer (our company) GST registration. Used to decide intra-state (CGST+SGST) vs inter-state (IGST).
    buyer_gstin: str = field(default_factory=lambda: _env("BUYER_GSTIN", "33AABCZ4321P1Z4"))  # Tamil Nadu (state 33), synthetic
    # Approval matrix: invoices above this INR value need a Finance Controller sign-off even when every check passes.
    approval_limit_inr: Decimal = field(default_factory=lambda: Decimal(_env("APPROVAL_LIMIT_INR", "500000")))
    # Indicative FX to INR for the approval matrix only (never used to compare invoice vs PO amounts). Assumption.
    fx_to_inr: dict = field(default_factory=lambda: {"INR": Decimal("1"), "USD": Decimal("84"), "EUR": Decimal("92"), "GBP": Decimal("108")})
    # A vendor-master bank change younger than this (days before the invoice) and not verified by call-back holds the invoice.
    bank_change_cooldown_days: int = field(default_factory=lambda: int(_env("BANK_CHANGE_COOLDOWN_DAYS", "30")))

    # ---- Jev (TypeSafe) model: advisory only. Never approves; can only add a hold or a suggestion for a person.
    # JEV_MODE: off | replay | live.  replay = recorded real answers (+ deterministic fake for unseen questions), no network.
    # JEV_ENABLED=1 is shorthand for live (falls back to replay when no key is set); JEV_ENABLED=0 turns it off.
    jev_mode: str = field(default_factory=lambda: _jev_mode())
    jev_base_url: str = field(default_factory=lambda: _env("JEV_BASE_URL", _env("TYPESAFE_BASE_URL", "https://api.typesafe.ai")))
    jev_model: str = field(default_factory=lambda: _env("JEV_MODEL", _env("TYPESAFE_DEFAULT_MODEL", "jev-latest")))
    jev_timeout_s: float = field(default_factory=lambda: float(_env("JEV_TIMEOUT_S", "10")))
    jev_replay_file: str = field(default_factory=lambda: _env("JEV_REPLAY_FILE", ""))
    jev_record: bool = field(default_factory=lambda: _env("JEV_RECORD", "0") == "1")
    jev_price_per_mtok_usd: float = field(default_factory=lambda: float(_env("JEV_PRICE_PER_MTOK_USD", "0.042")))
    # Calibrated gates (chosen on docs/EVALS.md). Below a gate the model's answer is not acted on; a person decides.
    jev_dup_hold_p: float = field(default_factory=lambda: float(_env("JEV_DUP_HOLD_P", "0.80")))
    jev_po_min_p: float = field(default_factory=lambda: float(_env("JEV_PO_MIN_P", "0.80")))
    jev_field_min_p: float = field(default_factory=lambda: float(_env("JEV_FIELD_MIN_P", "0.80")))

    # ---- Production switches (all default to the simple demo mode)
    auth_mode: str = field(default_factory=lambda: _env("AUTH_MODE", "off"))  # off | apikey
    api_keys: str = field(default_factory=lambda: _env("API_KEYS", ""))  # "key:role[:name],..."  roles: viewer < operator < reviewer < approver < admin
    rate_limit_per_min: int = field(default_factory=lambda: int(_env("RATE_LIMIT_PER_MIN", "0")))  # 0 = off
    worker_mode: str = field(default_factory=lambda: _env("WORKER_MODE", "inline"))  # inline (in the API process) | external (python -m app.worker)
    job_lease_s: float = field(default_factory=lambda: float(_env("JOB_LEASE_SECONDS", "120")))
    db_migrate: str = field(default_factory=lambda: _env("DB_MIGRATE", "create_all"))  # create_all | alembic
    otel_enabled: bool = field(default_factory=lambda: _env("OTEL_ENABLED", "0") == "1")
    log_format: str = field(default_factory=lambda: _env("LOG_FORMAT", "text"))  # text | json
    redact_pii: bool = field(default_factory=lambda: _env("REDACT_PII", "1") == "1")

    ruleset_version: str = "rules-v3"
    tolerances: Tolerances = field(default_factory=Tolerances)


settings = Settings()
