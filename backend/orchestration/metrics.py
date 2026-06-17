"""
Orchestration metrics.

An in-process metrics registry that records the required orchestration KPIs:

    workflow_count          counter, labelled by workflow name
    workflow_failures       counter, labelled by workflow name
    workflow_latency        histogram (list of durations) by workflow name
    retry_count             counter, labelled by workflow name
    job_success_rate        derived gauge (completed / (completed + failed))
    orchestration_errors    counter of internal orchestrator faults

The registry is intentionally simple (thread-safe dicts) and exposes
`snapshot()` returning a JSON-serialisable structure. A Prometheus/StatsD
exporter can wrap this without changing call sites.
"""

from __future__ import annotations

import threading
from collections import defaultdict
from statistics import mean
from typing import Any, Dict, List


class Metrics:
    def __init__(self):
        self._lock = threading.RLock()
        self.workflow_count: Dict[str, int] = defaultdict(int)
        self.workflow_failures: Dict[str, int] = defaultdict(int)
        self.workflow_completed: Dict[str, int] = defaultdict(int)
        self.workflow_latency: Dict[str, List[float]] = defaultdict(list)
        self.retry_count: Dict[str, int] = defaultdict(int)
        self.orchestration_errors: int = 0

    def record_start(self, workflow: str) -> None:
        with self._lock:
            self.workflow_count[workflow] += 1

    def record_completion(self, workflow: str, latency_s: float) -> None:
        with self._lock:
            self.workflow_completed[workflow] += 1
            self.workflow_latency[workflow].append(latency_s)

    def record_failure(self, workflow: str, latency_s: float = 0.0) -> None:
        with self._lock:
            self.workflow_failures[workflow] += 1
            if latency_s:
                self.workflow_latency[workflow].append(latency_s)

    def record_retry(self, workflow: str) -> None:
        with self._lock:
            self.retry_count[workflow] += 1

    def record_orchestration_error(self) -> None:
        with self._lock:
            self.orchestration_errors += 1

    def job_success_rate(self, workflow: str = None) -> float:
        with self._lock:
            if workflow:
                completed = self.workflow_completed[workflow]
                failed = self.workflow_failures[workflow]
            else:
                completed = sum(self.workflow_completed.values())
                failed = sum(self.workflow_failures.values())
            total = completed + failed
            return (completed / total) if total else 0.0

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            latency_summary = {
                wf: {
                    "count": len(vals),
                    "avg_s": round(mean(vals), 6) if vals else 0.0,
                    "max_s": round(max(vals), 6) if vals else 0.0,
                    "min_s": round(min(vals), 6) if vals else 0.0,
                }
                for wf, vals in self.workflow_latency.items()
            }
            return {
                "workflow_count": dict(self.workflow_count),
                "workflow_failures": dict(self.workflow_failures),
                "workflow_completed": dict(self.workflow_completed),
                "workflow_latency": latency_summary,
                "retry_count": dict(self.retry_count),
                "orchestration_errors": self.orchestration_errors,
                "job_success_rate": round(self.job_success_rate(), 4),
            }
