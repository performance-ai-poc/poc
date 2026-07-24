# Notes for other teams

A couple of things that touch other parts of the system.

## service.name is reported two ways

The orchestrator's OTLP spans carry `service.name = "orchestrator-svc"` (from
`OTEL_SERVICE_NAME`), but its stdout logs carry `service.name = "backend-api"`
(the `SERVICE_NAME` constant in `orchestrator-svc/app/logging_utils.py`). So a
trace and that same service's logs end up tagged with different names. The
Collector passes both through as emitted; someone should pick one value and set
both `OTEL_SERVICE_NAME` and the logger constant to it.

## route.reason_code is not emitted yet

The router in `app/orchestrator/routing.py::pick_agent` decides which agent
handles a message, but the reason is not recorded anywhere. Adding a small
enum attribute (e.g. `DOCUMENT_INTENT`, `SUPPORT_INTENT`, `API_INTENT`,
`FALLBACK_DEFAULT`) on the `invoke_agent` span is the one piece of context the
telemetry can't reconstruct on its own. Enum only — never free text or model
reasoning.

## Wiring more OTLP export

The Collector accepts OTLP on `4317` (gRPC) and `4318` (HTTP). Any service that
wants to export just points the standard `OTEL_EXPORTER_OTLP_*` environment
variables at it. On Kubernetes it should target the node's own Collector via the
downward API (`status.hostIP`), since the Collector runs as a per-node DaemonSet
on a hostPort rather than behind a Service.
