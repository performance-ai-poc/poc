"""Minimal OTLP-only OpenTelemetry setup for the MCP service."""

from __future__ import annotations

import logging
import os
from typing import Any

from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Span, Status, StatusCode

_configured = False
tracer = trace.get_tracer("performance-ai-poc.mcp-server")
meter = metrics.get_meter("performance-ai-poc.mcp-server")
tool_count = meter.create_counter("app.tool.count", unit="{call}")


def _endpoint() -> str | None:
    return os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT") or os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")


def _enabled(signal_name: str) -> bool:
    if os.getenv("OTEL_SDK_DISABLED", "false").lower() == "true":
        return False
    return os.getenv(f"OTEL_{signal_name}_EXPORTER", "otlp").lower() != "none" and _endpoint() is not None


def configure_telemetry(service_name: str) -> None:
    global _configured, tracer, meter
    if _configured:
        return

    resource = Resource.create(
        {
            SERVICE_NAME: os.getenv("OTEL_SERVICE_NAME", service_name),
            "service.namespace": "performance-ai-poc",
            "service.version": os.getenv("OTEL_SERVICE_VERSION", "0.1.0"),
            "deployment.environment.name": os.getenv("OTEL_DEPLOYMENT_ENVIRONMENT", "development"),
        }
    )
    endpoint = _endpoint()

    tracer_provider = TracerProvider(resource=resource)
    if endpoint and _enabled("TRACES"):
        tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    trace.set_tracer_provider(tracer_provider)

    readers = []
    if endpoint and _enabled("METRICS"):
        readers.append(PeriodicExportingMetricReader(OTLPMetricExporter(endpoint=endpoint)))
    metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=readers))

    if endpoint and _enabled("LOGS"):
        logger_provider = LoggerProvider(resource=resource)
        logger_provider.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter(endpoint=endpoint)))
        logging.getLogger("mcp-server").addHandler(
            LoggingHandler(level=logging.NOTSET, logger_provider=logger_provider)
        )

    tracer = trace.get_tracer("performance-ai-poc.mcp-server")
    meter = metrics.get_meter("performance-ai-poc.mcp-server")
    _configured = True


def span_attributes(ids: dict[str, str | None], tool: str) -> dict[str, Any]:
    attributes = {
        "gen_ai.tool.name": tool,
        "gen_ai.tool.type": "function",
        **{f"app.{key}": value for key, value in ids.items() if value is not None},
    }
    if tool == "search_documents":
        attributes.update(
            {
                "gen_ai.operation.name": "retrieval",
                "gen_ai.data_source.id": "postgresql",
            }
        )
    elif tool in {"get_schema", "run_query"}:
        attributes["db.system.name"] = "postgresql"
    return attributes


def record_error(span: Span, exc: BaseException) -> None:
    span.record_exception(exc)
    span.set_status(Status(StatusCode.ERROR, type(exc).__name__))


def extract_context(ctx: Any):
    """Rebuild the OTel context from the W3C trace headers the orchestrator put
    in the request ``_meta``, so a tool span parents onto the caller's span.
    Returns ``None`` (use the current context) when none is present; never raises."""
    try:
        from opentelemetry.propagate import extract

        meta = ctx.request_context.meta if ctx is not None else None
        if meta is None:
            return None
        carrier = {}
        for key in ("traceparent", "tracestate", "baggage"):
            value = getattr(meta, key, None)
            if isinstance(value, str):
                carrier[key] = value
        if not carrier:
            return None
        return extract(carrier)
    except Exception:  # noqa: BLE001 — never fail a tool call over propagation.
        return None
