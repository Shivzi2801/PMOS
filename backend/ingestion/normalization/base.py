"""Base normalizer contract and shared text-cleaning helpers."""

from __future__ import annotations

import re
import unicodedata
from abc import ABC, abstractmethod
from typing import Any, Dict

from ..contracts import CanonicalDocument


class NormalizationError(Exception):
    """Raised when a raw payload cannot be normalized into a CanonicalDocument."""


# Characters frequently used to smuggle hidden prompt-injection payloads.
# We strip them during normalization so screening sees the real text, and so
# the canonical body is clean. The injection screener also flags their presence.
_ZERO_WIDTH = "".join(
    [
        "\u200b",  # zero-width space
        "\u200c",  # zero-width non-joiner
        "\u200d",  # zero-width joiner
        "\u2060",  # word joiner
        "\ufeff",  # zero-width no-break space / BOM
    ]
)
_ZERO_WIDTH_RE = re.compile(f"[{_ZERO_WIDTH}]")
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_MULTI_WS_RE = re.compile(r"[ \t\u00a0]+")
_MULTI_NL_RE = re.compile(r"\n{3,}")


def strip_zero_width(text: str) -> str:
    """Remove zero-width / invisible characters used for hidden prompts."""
    return _ZERO_WIDTH_RE.sub("", text)


def strip_html(text: str) -> str:
    """Remove HTML tags, leaving readable text."""
    return _HTML_TAG_RE.sub(" ", text)


def normalize_unicode(text: str) -> str:
    """Apply NFKC normalization to fold lookalike / compatibility characters."""
    return unicodedata.normalize("NFKC", text)


def clean_text(text: str, *, allow_html: bool = False) -> str:
    """Standard text cleaning pipeline applied to every connector body."""
    if text is None:
        return ""
    text = normalize_unicode(text)
    text = strip_zero_width(text)
    if not allow_html:
        text = strip_html(text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _MULTI_WS_RE.sub(" ", text)
    text = _MULTI_NL_RE.sub("\n\n", text)
    return text.strip()


class Normalizer(ABC):
    """Abstract base for connector-specific normalizers."""

    #: connector_type this normalizer handles, e.g. "zendesk"
    connector_type: str = ""

    @abstractmethod
    def normalize(self, raw: Dict[str, Any], *, connector_id: str) -> CanonicalDocument:
        """Convert a single raw connector record into a CanonicalDocument.

        Raises:
            NormalizationError: if required fields are missing or malformed.
        """
        raise NotImplementedError

    def supports(self, connector_type: str) -> bool:
        return connector_type == self.connector_type
