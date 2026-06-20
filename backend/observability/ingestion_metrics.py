"""
PMOS Observability & Monitoring — Ingestion Metrics (S2.6)

Typed facade spanning the ingestion pipeline slices (S1.1 Connectors through
S1.5 Indexing). The ``stage`` label distinguishes the pipeline phase
(connect / process / extract / resolve / index) so a single dashboard can
break ingestion latency and throughput down by stage and connector.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .metrics_collector import MetricsCollector


class IngestionStage(str, Enum):
    CONNECT = "connect"
    PROCESS = "process"
    EXTRACT = "extract"
    RESOLVE = "resolve"
    INDEX = "index"


@dataclass(frozen=True)
class IngestionOutcome:
    stage: IngestionStage
    connector: str
    tenant_id: str
    duration_ms: float
    success: bool = True
    chunks: int = 0


class IngestionMetrics:
    M_LATENCY = "pmos.ingestion.latency_ms"
    M_DOCUMENTS = "pmos.ingestion.documents_total"
    M_CHUNKS = "pmos.ingestion.chunks_total"

    def __init__(self, collector: MetricsCollector) -> None:
        self._collector = collector

    def record(self, outcome: IngestionOutcome) -> None:
        self._collector.observe(
            self.M_LATENCY, outcome.duration_ms,
            labels={
                "stage": outcome.stage.value,
                "connector": outcome.connector,
                "tenant": outcome.tenant_id,
            },
        )
        self._collector.increment(
            self.M_DOCUMENTS,
            labels={
                "stage": outcome.stage.value,
                "connector": outcome.connector,
                "status": "success" if outcome.success else "error",
                "tenant": outcome.tenant_id,
            },
        )
        if outcome.chunks:
            self._collector.increment(
                self.M_CHUNKS, amount=float(outcome.chunks),
                labels={"connector": outcome.connector, "tenant": outcome.tenant_id},
            )


__all__ = ["IngestionMetrics", "IngestionOutcome", "IngestionStage"]
