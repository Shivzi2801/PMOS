"""Normalization layer.

Converts connector-specific raw payloads into canonical PMOS documents while
preserving source metadata, provenance, and connector identifiers.
"""

from .base import Normalizer, NormalizationError
from .registry import NormalizerRegistry, registry
from .zendesk import ZendeskNormalizer

# Register built-in normalizers on import.
registry.register(ZendeskNormalizer())

__all__ = [
    "Normalizer",
    "NormalizationError",
    "NormalizerRegistry",
    "registry",
    "ZendeskNormalizer",
]
