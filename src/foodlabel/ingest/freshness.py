"""Data freshness: when the index was built, from which document versions, how many chunks (D2).

Exposed as Prometheus gauges by a collector that reads the manifest at scrape time, so the API,
worker and verifier all report the index they are actually serving.
"""
from __future__ import annotations

import json
import os
from typing import Optional

from foodlabel.common.config import get_settings

_registered = False


def manifest_path(index_dir: Optional[str] = None) -> str:
    return os.path.join(index_dir or get_settings().index_dir, "manifest.json")


def read_manifest(index_dir: Optional[str] = None) -> Optional[dict]:
    try:
        with open(manifest_path(index_dir), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def index_build_id(index_dir: Optional[str] = None) -> Optional[str]:
    m = read_manifest(index_dir)
    return m["build_id"] if m else None


class IndexFreshnessCollector:
    def collect(self):
        from prometheus_client.core import GaugeMetricFamily

        m = read_manifest() or {}
        g = GaugeMetricFamily("foodlabel_index_built_timestamp_seconds", "Unix time the serving index was built")
        g.add_metric([], m.get("built_at", 0))
        yield g
        g = GaugeMetricFamily("foodlabel_index_chunks", "Chunks in the serving index")
        g.add_metric([], m.get("chunk_count", 0))
        yield g
        g = GaugeMetricFamily("foodlabel_index_documents", "Documents in the serving index")
        g.add_metric([], len(m.get("documents", [])))
        yield g
        g = GaugeMetricFamily("foodlabel_ingest_throughput_chunks_per_second", "Throughput of the last ingestion run")
        g.add_metric([], (m.get("throughput") or {}).get("chunks_per_second", 0))
        yield g


def register_collector() -> None:
    global _registered
    if _registered:
        return
    from prometheus_client import REGISTRY

    REGISTRY.register(IndexFreshnessCollector())
    _registered = True
