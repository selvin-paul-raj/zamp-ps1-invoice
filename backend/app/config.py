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
    ruleset_version: str = "rules-v2"
    tolerances: Tolerances = field(default_factory=Tolerances)


settings = Settings()
