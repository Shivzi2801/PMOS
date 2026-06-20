"""
PMOS Observability & Monitoring — Trace Context (S2.6)

A :class:`TraceContext` is the immutable identity of a position within a
distributed trace: the trace id, the current span id, sampling decision and
baggage. It is what crosses process/service boundaries (carried in HTTP headers
or message metadata) so that spans produced by different PMOS slices stitch
into one tree.

Propagation uses the W3C ``traceparent`` / ``tracestate`` shape so PMOS
interoperates with standard tooling, but the implementation is self-contained
and has no OpenTelemetry dependency.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field, replace
from typing import Dict, Mapping, Optional

from .errors import TraceContextError

_TRACE_ID_BYTES = 16
_SPAN_ID_BYTES = 8

_TRACEPARENT_RE = re.compile(
    r"^(?P<version>[0-9a-f]{2})-"
    r"(?P<trace_id>[0-9a-f]{32})-"
    r"(?P<span_id>[0-9a-f]{16})-"
    r"(?P<flags>[0-9a-f]{2})$"
)

# W3C header names.
TRACEPARENT_HEADER = "traceparent"
TRACESTATE_HEADER = "tracestate"

_FLAG_SAMPLED = 0x01


def generate_trace_id() -> str:
    return os.urandom(_TRACE_ID_BYTES).hex()


def generate_span_id() -> str:
    return os.urandom(_SPAN_ID_BYTES).hex()


@dataclass(frozen=True)
class TraceContext:
    """Immutable trace position.

    Attributes
    ----------
    trace_id:
        32-hex-char trace identifier shared by every span in the trace.
    span_id:
        16-hex-char identifier of the *current* span (the parent for any new
        child created from this context).
    sampled:
        Whether this trace is recorded.
    baggage:
        Key/value pairs propagated alongside the trace (e.g. tenant id).
    remote:
        True if this context was extracted from an inbound carrier rather than
        created locally.
    """

    trace_id: str
    span_id: str
    sampled: bool = True
    baggage: Mapping[str, str] = field(default_factory=dict)
    remote: bool = False

    @classmethod
    def new_root(cls, *, sampled: bool = True) -> "TraceContext":
        return cls(
            trace_id=generate_trace_id(),
            span_id=generate_span_id(),
            sampled=sampled,
            baggage={},
            remote=False,
        )

    def child(self, span_id: Optional[str] = None) -> "TraceContext":
        """Derive a context for a child span (same trace, new span id)."""
        return replace(
            self,
            span_id=span_id or generate_span_id(),
            remote=False,
        )

    def with_baggage(self, **items: str) -> "TraceContext":
        merged = dict(self.baggage)
        merged.update({k: str(v) for k, v in items.items()})
        return replace(self, baggage=merged)

    # -- propagation ------------------------------------------------------

    def to_headers(self) -> Dict[str, str]:
        """Serialize to W3C-style headers for outbound propagation."""
        flags = _FLAG_SAMPLED if self.sampled else 0x00
        headers = {
            TRACEPARENT_HEADER: f"00-{self.trace_id}-{self.span_id}-{flags:02x}",
        }
        if self.baggage:
            headers[TRACESTATE_HEADER] = ",".join(
                f"{k}={v}" for k, v in self.baggage.items()
            )
        return headers

    @classmethod
    def from_headers(cls, headers: Mapping[str, str]) -> Optional["TraceContext"]:
        """Extract a context from inbound headers.

        Returns ``None`` if no traceparent is present (caller starts a root).
        Raises :class:`TraceContextError` on a malformed traceparent.
        """
        # Case-insensitive lookup.
        lowered = {k.lower(): v for k, v in headers.items()}
        raw = lowered.get(TRACEPARENT_HEADER)
        if raw is None:
            return None
        match = _TRACEPARENT_RE.match(raw.strip())
        if not match:
            raise TraceContextError(
                "Malformed traceparent header",
                details={"traceparent": raw},
            )
        trace_id = match.group("trace_id")
        span_id = match.group("span_id")
        if trace_id == "0" * 32 or span_id == "0" * 16:
            raise TraceContextError(
                "traceparent contains an all-zero id",
                details={"traceparent": raw},
            )
        flags = int(match.group("flags"), 16)
        sampled = bool(flags & _FLAG_SAMPLED)

        baggage: Dict[str, str] = {}
        state = lowered.get(TRACESTATE_HEADER)
        if state:
            for pair in state.split(","):
                pair = pair.strip()
                if not pair or "=" not in pair:
                    continue
                k, _, v = pair.partition("=")
                baggage[k.strip()] = v.strip()

        return cls(
            trace_id=trace_id,
            span_id=span_id,
            sampled=sampled,
            baggage=baggage,
            remote=True,
        )


__all__ = [
    "TraceContext",
    "generate_trace_id",
    "generate_span_id",
    "TRACEPARENT_HEADER",
    "TRACESTATE_HEADER",
]
