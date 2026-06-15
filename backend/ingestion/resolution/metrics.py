"""Metrics for PMOS Wave 1 Slice 1.4.

A small, dependency-free counter bag. The merge pipeline increments these
as it processes atoms; callers can snapshot them for emission to the
platform metrics sink.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ResolutionMetrics:
    """Mutable counters tracked across a resolution run."""

    entities_processed: int = 0
    entities_merged: int = 0
    acl_rejections: int = 0
    provenance_records_created: int = 0

    def record_processed(self, n: int = 1) -> None:
        self.entities_processed += n

    def record_merged(self, n: int = 1) -> None:
        self.entities_merged += n

    def record_acl_rejection(self, n: int = 1) -> None:
        self.acl_rejections += n

    def record_provenance(self, n: int = 1) -> None:
        self.provenance_records_created += n

    @property
    def merge_rate(self) -> float:
        """Fraction of processed atoms that merged into an existing entity.

        Returns 0.0 when nothing has been processed to avoid division by
        zero. merge_rate = entities_merged / entities_processed.
        """
        if self.entities_processed == 0:
            return 0.0
        return self.entities_merged / self.entities_processed

    def snapshot(self) -> dict:
        return {
            "entities_processed": self.entities_processed,
            "entities_merged": self.entities_merged,
            "merge_rate": self.merge_rate,
            "acl_rejections": self.acl_rejections,
            "provenance_records_created": self.provenance_records_created,
        }
