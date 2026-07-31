def test_metric_value_normalizes_dotted_otel_metric_name():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)

        return httpx.Response(
            200,
            json={"hits": [{"value": 0.41}]},
        )

    val = metric_value(
        "system.cpu.utilization",
        0,
        1,
        client=_client(handler),
    )

    assert val == pytest.approx(0.41)

    sql = seen["body"]["query"]["sql"]

    assert '"system_cpu_utilization"' in sql
    assert '"system.cpu.utilization"' not in sql
    assert "type=metrics" in seen["url"]