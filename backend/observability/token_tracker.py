"""
PMOS Observability & Monitoring — Token Tracker (S2.6)

In-memory accounting of token consumption, broken down by tenant, model, and
token kind (prompt vs completion). While token counts also flow into histogram
metrics, the tracker keeps an *exact* running ledger suitable for billing-grade
usage reports, which sampled/bucketed metrics cannot provide.
"""

from __future__ import annotations

import threading
from collections import defaultdict
from dataclasses import dataclass
from enum import Enum
from typing import Dict, Iterable, List, Tuple


class TokenKind(str, Enum):
    PROMPT = "prompt"
    COMPLETION = "completion"


@dataclass(frozen=True)
class TokenLedgerEntry:
    tenant_id: str
    model: str
    prompt_tokens: int
    completion_tokens: int

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class TokenTracker:
    """Exact, thread-safe token ledger keyed by (tenant, model)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # (tenant, model) -> [prompt, completion]
        self._ledger: Dict[Tuple[str, str], List[int]] = defaultdict(lambda: [0, 0])

    def record(
        self,
        *,
        tenant_id: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> None:
        if prompt_tokens < 0 or completion_tokens < 0:
            raise ValueError("token counts must be non-negative")
        with self._lock:
            entry = self._ledger[(tenant_id, model)]
            entry[0] += prompt_tokens
            entry[1] += completion_tokens

    def entries(self) -> Tuple[TokenLedgerEntry, ...]:
        with self._lock:
            return tuple(
                TokenLedgerEntry(
                    tenant_id=tenant,
                    model=model,
                    prompt_tokens=counts[0],
                    completion_tokens=counts[1],
                )
                for (tenant, model), counts in self._ledger.items()
            )

    def for_tenant(self, tenant_id: str) -> Tuple[TokenLedgerEntry, ...]:
        return tuple(e for e in self.entries() if e.tenant_id == tenant_id)

    def total_tokens(self) -> int:
        with self._lock:
            return sum(p + c for p, c in self._ledger.values())

    def tenant_total(self, tenant_id: str) -> int:
        return sum(e.total_tokens for e in self.for_tenant(tenant_id))

    def reset(self) -> None:
        with self._lock:
            self._ledger.clear()


__all__ = ["TokenTracker", "TokenKind", "TokenLedgerEntry"]
