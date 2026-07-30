"""Vendor-neutral OpenTelemetry setup and small instrumentation helpers.

The application exports OTLP only. Backend selection belongs to the Collector,
so this module has no knowledge of Langfuse, Grafana, Jaeger, or any other
observability product.
"""

from __future__ import annotations

import functools
import inspect
import logging
import os
import time
from typing import Any, Callable, TypeVar

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
from app.config import settings

from app.context import RequestContext

_SERVICE_NAMESPACE = "performance-ai-poc"
_TRACER_NAME = "performance-ai-poc.orchestrator"
_METER_NAME = "performance-ai-poc.orchestrator"
_T = TypeVar("_T")
_configured = False
_httpx_instrumented = False

tracer = trace.get_tracer(_TRACER_NAME)
meter = metrics.get_meter(_METER_NAME)
workflow_count = meter.create_counter("app.workflow.count", unit="{workflow}")
workflow_duration = meter.create_histogram("app.workflow.duration", unit="s")
llm_token_usage = meter.create_counter("app.llm.token.usage", unit="{token}")
tool_count = meter.create_counter("app.tool.count", unit="{call}")


def _endpoint() -> str | None:
    return settings.otel_exporter_otlp_traces_endpoint or settings.otel_exporter_otlp_endpoint


def _enabled(signal_name: str) -> bool:
    if settings.otel_sdk_disabled:
        return False
    exporter = getattr(settings, f"otel_{signal_name.lower()}_exporter", "otlp")
    return exporter.lower() != "none" and _endpoint() is not None


def configure_telemetry(service_name: str) -> None:
    """Configure SDK providers once, with OTLP enabled only when configured."""
    global _configured, tracer, meter
    if _configured:
        return

    resource = Resource.create(
        {
            SERVICE_NAME: settings.otel_service_name or service_name,
            "service.namespace": _SERVICE_NAMESPACE,
            "service.version": settings.otel_service_version or "0.1.0",
            "deployment.environment.name": settings.otel_deployment_environment or "development",
        }
    )

    tracer_provider = TracerProvider(resource=resource)
    endpoint = _endpoint()
    if endpoint and _enabled("TRACES"):
        tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    trace.set_tracer_provider(tracer_provider)

    readers = []
    if endpoint and _enabled("METRICS"):
        readers.append(PeriodicExportingMetricReader(OTLPMetricExporter(endpoint=endpoint)))
    meter_provider = MeterProvider(resource=resource, metric_readers=readers)
    metrics.set_meter_provider(meter_provider)

    if endpoint and _enabled("LOGS"):
        logger_provider = LoggerProvider(resource=resource)
        logger_provider.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter(endpoint=endpoint)))
        logging.getLogger("backend").addHandler(LoggingHandler(level=logging.NOTSET, logger_provider=logger_provider))

    tracer = trace.get_tracer(_TRACER_NAME)
    meter = metrics.get_meter(_METER_NAME)
    _configured = True


def instrument_httpx() -> None:
    """Create standard client spans for LLM and MCP HTTP calls."""
    global _httpx_instrumented
    if _httpx_instrumented:
        return
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

    HTTPXClientInstrumentor().instrument()
    _httpx_instrumented = True


def instrument_fastapi(app: Any) -> None:
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    FastAPIInstrumentor().instrument_app(app)


def context_attributes(ctx: RequestContext | None) -> dict[str, Any]:
    if ctx is None:
        return {}
    return {
        "app.run_id": ctx.run_id,
        "app.request_id": ctx.request_id,
        "app.session_id": ctx.session_id,
        "app.tenant_id": ctx.tenant_id,
        "gen_ai.conversation.id": ctx.session_id,
    }


def record_error(span: Span, exc: BaseException, *, error_type: str | None = None) -> None:
    span.record_exception(exc)
    span.set_status(Status(StatusCode.ERROR, error_type or type(exc).__name__))
    if error_type:
        span.set_attribute("error.type", error_type)


def mark_failure(span: Span, error_type: str) -> None:
    span.set_status(Status(StatusCode.ERROR, error_type))
    span.set_attribute("error.type", error_type)


def trace_agent_step(fn: Callable[..., _T]) -> Callable[..., _T]:
    """Wrap sync or async agent nodes without putting OTel code in business logic."""
    if inspect.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def async_wrapper(state: dict) -> Any:
            step = state["steps"][state["current_step"]]
            attrs = {
                **context_attributes(state.get("ctx")),
                "gen_ai.operation.name": "invoke_agent",
                "gen_ai.agent.name": step["agent"],
                "app.agent.step.sequence": step["sequence"],
            }
            started = time.perf_counter()
            with tracer.start_as_current_span("invoke_agent", attributes=attrs) as span:
                try:
                    result = await fn(state)
                    outcome = result["step_results"].get(step["key"], {}).get("status", "unknown")
                    span.set_attribute("app.outcome", outcome)
                    if outcome in {"error", "failed"}:
                        mark_failure(span, "agent_step_failed")
                    return result
                except Exception as exc:
                    record_error(span, exc)
                    raise
                finally:
                    workflow_duration.record(time.perf_counter() - started, {"agent": step["agent"]})

        return async_wrapper  # type: ignore[return-value]

    @functools.wraps(fn)
    def sync_wrapper(state: dict) -> Any:
        step = state["steps"][state["current_step"]]
        attrs = {
            **context_attributes(state.get("ctx")),
            "gen_ai.operation.name": "invoke_agent",
            "gen_ai.agent.name": step["agent"],
            "app.agent.step.sequence": step["sequence"],
        }
        started = time.perf_counter()
        with tracer.start_as_current_span("invoke_agent", attributes=attrs) as span:
            try:
                result = fn(state)
                outcome = result["step_results"].get(step["key"], {}).get("status", "unknown")
                span.set_attribute("app.outcome", outcome)
                if outcome in {"error", "failed"}:
                    mark_failure(span, "agent_step_failed")
                return result
            except Exception as exc:
                record_error(span, exc)
                raise
            finally:
                workflow_duration.record(time.perf_counter() - started, {"agent": step["agent"]})

    return sync_wrapper  # type: ignore[return-value]
