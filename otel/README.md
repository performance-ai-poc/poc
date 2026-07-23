# OTel

OpenTelemetry Collector configuration and instrumentation notes for the AI chat
POC.

The application services export standard OTLP data to `collector-config.yaml`.
The initial Collector uses the `debug` exporter so the pipeline can be verified
locally without selecting an observability vendor.

Run it locally with:

```bash
docker run --rm \
  -p 4317:4317 -p 4318:4318 \
  -v "$(pwd)/otel/collector-config.yaml:/etc/otelcol-contrib/config.yaml" \
  otel/opentelemetry-collector-contrib:0.153.0
```

When a backend is selected, add its OTLP exporter and reference it from the
three pipelines here. The application code and its `OTEL_EXPORTER_*`
configuration do not need to change.

The Collector configuration should also document:

- Collector pipeline configuration.
- Redaction rules and sampling behavior.
- Agent, MCP, model, and API semantic event names.
- How `run_id`, `request_id`, `session_id`, and `tenant_id` map onto OTel traces, spans, logs, and attributes.
