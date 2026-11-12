"""Generate deploy/grafana/dashboards/foodlabel.json.

Edit the PANELS list, then run:  python deploy/grafana/build_dashboard.py
"""
import json
import pathlib

OUT = pathlib.Path(__file__).parent / "dashboards" / "foodlabel.json"
DS = {"type": "prometheus", "uid": "prom"}

# (title, [(expr, legend)], unit)
PANELS = [
    ("API latency p50 / p95", [
        ("histogram_quantile(0.50, sum(rate(http_request_duration_seconds_bucket{service=\"api\"}[1m])) by (le))", "p50"),
        ("histogram_quantile(0.95, sum(rate(http_request_duration_seconds_bucket{service=\"api\"}[1m])) by (le))", "p95")], "s"),
    ("Run end-to-end latency p50 / p95 (submission to result)", [
        ("histogram_quantile(0.50, sum(rate(run_end_to_end_seconds_bucket[2m])) by (le))", "p50"),
        ("histogram_quantile(0.95, sum(rate(run_end_to_end_seconds_bucket[2m])) by (le))", "p95")], "s"),
    ("Throughput (runs finished/s)", [("sum(rate(runs_finished_total[1m]))", "runs/s"),
                                       ("sum(rate(http_requests_total{service=\"api\"}[1m]))", "api req/s")], "reqps"),
    ("API error rate (5xx share)", [
        ("sum(rate(http_requests_total{service=\"api\",status=~\"5..\"}[1m])) / clamp_min(sum(rate(http_requests_total{service=\"api\"}[1m])), 1e-9)", "5xx")], "percentunit"),
    ("Availability (up)", [("up", "{{job}} {{instance}}")], "none"),
    ("Task success rate (completed, not degraded)", [
        ("sum(rate(runs_finished_total{status=\"completed\",degraded=\"False\"}[5m])) / clamp_min(sum(rate(runs_finished_total[5m])), 1e-9)", "success")], "percentunit"),
    ("Steps per run (avg)", [("rate(agent_steps_per_run_sum[5m]) / clamp_min(rate(agent_steps_per_run_count[5m]), 1e-9)", "steps")], "none"),
    ("Tool calls per run (avg)", [("rate(agent_tool_calls_per_run_sum[5m]) / clamp_min(rate(agent_tool_calls_per_run_count[5m]), 1e-9)", "tool calls")], "none"),
    ("Latency per step p95", [
        ("histogram_quantile(0.95, sum(rate(agent_step_duration_seconds_bucket[2m])) by (le, step))", "{{step}}")], "s"),
    ("LLM retry rate", [("sum(rate(llm_retries_total[1m])) / clamp_min(sum(rate(llm_calls_total[1m])), 1e-9)", "retries per call")], "percentunit"),
    ("Circuit breaker state (0 closed, 1 half-open, 2 open)", [("max(circuit_breaker_state) by (name)", "{{name}}")], "none"),
    ("Queue depth", [("run_queue_depth", "queued runs")], "none"),
    ("Cost per 100 runs (USD)", [
        ("100 * sum(increase(llm_cost_usd_total[10m])) / clamp_min(sum(increase(runs_finished_total[10m])), 1)", "per 100 runs")], "currencyUSD"),
    ("Cost by step (USD/min)", [("sum(rate(llm_cost_usd_total[5m])) by (step) * 60", "{{step}}")], "currencyUSD"),
    ("Cache hit rate", [
        ("sum(rate(cache_requests_total{result=\"hit\"}[5m])) by (cache) / clamp_min(sum(rate(cache_requests_total[5m])) by (cache), 1e-9)", "{{cache}}")], "percentunit"),
    ("Quarantined / rate-limited / guardrail events (per min)", [
        ("sum(rate(requests_quarantined_total[5m])) * 60", "quarantined"),
        ("sum(rate(rate_limited_total[5m])) * 60", "rate limited"),
        ("sum(rate(guardrail_events_total[5m])) * 60", "guardrail")], "none"),
    ("Live quality scores (sampled runs)", [("live_quality_score", "{{metric}}")], "percentunit"),
    ("Index freshness: chunks / age (s)", [("foodlabel_index_chunks", "chunks"),
                                           ("time() - foodlabel_index_built_timestamp_seconds", "age s")], "none"),
    ("CPU per container (cores)", [("sum(rate(container_cpu_usage_seconds_total{name=~\".+\"}[1m])) by (name)", "{{name}}")], "none"),
    ("Memory per container", [("sum(container_memory_working_set_bytes{name=~\".+\"}) by (name)", "{{name}}")], "bytes"),
    ("Container uptime", [("time() - container_start_time_seconds{name=~\".+\"}", "{{name}}")], "s"),
]


def panel(i, title, queries, unit):
    return {
        "id": i + 1, "type": "timeseries", "title": title, "datasource": DS,
        "gridPos": {"h": 8, "w": 12, "x": (i % 2) * 12, "y": (i // 2) * 8},
        "fieldConfig": {"defaults": {"unit": unit}, "overrides": []},
        "targets": [{"refId": chr(65 + j), "expr": e, "legendFormat": l, "datasource": DS}
                    for j, (e, l) in enumerate(queries)],
    }


dash = {
    "uid": "foodlabel", "title": "Food Label Compliance - Service", "schemaVersion": 39, "version": 1,
    "refresh": "10s", "time": {"from": "now-30m", "to": "now"}, "tags": ["foodlabel"],
    "panels": [panel(i, *p) for i, p in enumerate(PANELS)],
}
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(dash, indent=2) + "\n", encoding="utf-8")
print("wrote", OUT)
