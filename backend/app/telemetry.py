"""Tracing and structured logs.

* OpenTelemetry tracing: one span per run and per pipeline step, and one span per model call using the
  OpenTelemetry GenAI semantic conventions (gen_ai.system, gen_ai.request.model, gen_ai.response.model,
  gen_ai.usage.input_tokens ...). Off by default. Turn on with OTEL_ENABLED=1; spans go to
  OTEL_EXPORTER_OTLP_ENDPOINT (any OTLP backend: Jaeger, Grafana Tempo, Honeycomb, or Langfuse, which
  accepts OTLP at /api/public/otel) or to the console when no endpoint is set.
  With the SDK not installed or tracing off, `span()` is a no-op (the OTel API's default tracer).
* Structured logs: LOG_FORMAT=json prints one JSON object per line with the current trace id.
"""
from __future__ import annotations

import json
import logging
import os
from contextlib import contextmanager
from typing import Any, Iterator

try:
    from opentelemetry import trace as _trace
except Exception:  # pragma: no cover - OTel API missing entirely
    _trace = None

_configured = False


def setup(service_name: str = "zamp-ps1-invoice") -> str:
    """Configure tracing + logging from env. Returns a short description for /ready."""
    global _configured
    fmt = os.environ.get("LOG_FORMAT", "text")
    root = logging.getLogger()
    if fmt == "json":
        h = logging.StreamHandler()
        h.setFormatter(JsonFormatter())
        root.handlers = [h]
        root.setLevel(logging.INFO)
    if os.environ.get("OTEL_ENABLED", "0") != "1" or _trace is None:
        return "off"
    if _configured:
        return "on"
    try:
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
    except Exception:  # pragma: no cover
        logging.getLogger("zamp_ps1").warning("OTEL_ENABLED=1 but opentelemetry-sdk is not installed; tracing stays off")
        return "sdk-missing"
    provider = TracerProvider(resource=Resource.create({"service.name": os.environ.get("OTEL_SERVICE_NAME", service_name)}))
    exporter = None
    if os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

            exporter = OTLPSpanExporter()
        except Exception:  # pragma: no cover
            logging.getLogger("zamp_ps1").warning("OTLP exporter not installed (pip install opentelemetry-exporter-otlp-proto-http); using console")
    provider.add_span_processor(BatchSpanProcessor(exporter or ConsoleSpanExporter()))
    _trace.set_tracer_provider(provider)
    _configured = True
    return "on"


class _NoSpan:
    def set_attribute(self, *_a, **_k) -> None:
        pass

    def record_exception(self, *_a, **_k) -> None:
        pass


@contextmanager
def span(name: str, attrs: dict[str, Any] | None = None) -> Iterator[Any]:
    if _trace is None:
        yield _NoSpan()
        return
    tracer = _trace.get_tracer("zamp_ps1")
    with tracer.start_as_current_span(name) as sp:
        for k, v in (attrs or {}).items():
            if v is not None:
                sp.set_attribute(k, v if isinstance(v, (str, int, float, bool)) else str(v))
        yield sp


def current_trace_id() -> str | None:
    if _trace is None:
        return None
    ctx = _trace.get_current_span().get_span_context()
    return format(ctx.trace_id, "032x") if ctx and ctx.trace_id else None


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        d = {"ts": self.formatTime(record), "level": record.levelname, "logger": record.name, "msg": record.getMessage()}
        tid = current_trace_id()
        if tid:
            d["trace_id"] = tid
        for k in ("run_id", "invoice_id", "path", "status", "duration_ms", "client"):
            if hasattr(record, k):
                d[k] = getattr(record, k)
        if record.exc_info:
            d["exc"] = self.formatException(record.exc_info)
        return json.dumps(d, default=str)
