"""Registry that maps a connector_type to its Normalizer implementation."""

from __future__ import annotations

from typing import Any, Dict

from ..contracts import CanonicalDocument
from .base import Normalizer, NormalizationError


class NormalizerRegistry:
    """Holds and dispatches to registered normalizers by connector_type."""

    def __init__(self) -> None:
        self._normalizers: Dict[str, Normalizer] = {}

    def register(self, normalizer: Normalizer) -> None:
        if not normalizer.connector_type:
            raise ValueError("normalizer.connector_type must be set")
        self._normalizers[normalizer.connector_type] = normalizer

    def get(self, connector_type: str) -> Normalizer:
        try:
            return self._normalizers[connector_type]
        except KeyError:
            raise NormalizationError(
                f"no normalizer registered for connector_type={connector_type!r}"
            )

    def normalize(
        self, connector_type: str, raw: Dict[str, Any], *, connector_id: str
    ) -> CanonicalDocument:
        return self.get(connector_type).normalize(raw, connector_id=connector_id)

    def registered_types(self) -> list[str]:
        return sorted(self._normalizers.keys())


#: Process-wide default registry.
registry = NormalizerRegistry()
