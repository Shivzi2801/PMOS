"""
backend/retrieval/query_expansion.py

Query-expansion hooks (S1.6, responsibility #6).

Query expansion widens recall by deriving additional terms or alternate phrasings
from the original query before it hits the vector store. This module provides:

* A ``QueryExpander`` Protocol -- the extension point.
* ``NoopExpander`` -- the default (returns the query unchanged).
* ``SynonymExpander`` -- a dependency-free, dictionary-driven expander useful
  for tests and lightweight deployments.
* ``CompositeExpander`` -- chains expanders.
* ``safe_expand`` -- runs an expander but never lets an expansion failure break
  retrieval; on error it falls back to the original query and records the
  degradation (responsibility #8 + extension-point friendliness).

A future LLM-based expander simply implements ``QueryExpander`` and is dropped
in via configuration -- no changes to the retriever required.

No external dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Mapping, Protocol, Sequence, Tuple, runtime_checkable

from .errors import QueryExpansionError
from .retrieval_query import RetrievalQuery


@dataclass(frozen=True)
class ExpansionResult:
    """Output of an expander: the (possibly rewritten) query + new terms."""

    query: RetrievalQuery
    expanded_terms: Tuple[str, ...] = ()
    degraded: bool = False
    note: str = ""


@runtime_checkable
class QueryExpander(Protocol):
    """Extension point for query expansion strategies."""

    def expand(self, query: RetrievalQuery) -> ExpansionResult:  # pragma: no cover
        ...


class NoopExpander:
    """Default expander: identity. Always safe."""

    def expand(self, query: RetrievalQuery) -> ExpansionResult:
        return ExpansionResult(query=query, expanded_terms=())


@dataclass
class SynonymExpander:
    """
    Token-level synonym expansion driven by a static map.

    For each token in the query that appears in ``synonyms``, the mapped terms
    are appended to the query text. Case-insensitive on lookup; deterministic
    output ordering for testability.
    """

    synonyms: Mapping[str, Sequence[str]] = field(default_factory=dict)
    max_added_terms: int = 8

    def expand(self, query: RetrievalQuery) -> ExpansionResult:
        tokens = query.text.split()
        added: List[str] = []
        seen = {t.lower() for t in tokens}
        for tok in tokens:
            for syn in self.synonyms.get(tok.lower(), ()):  # type: ignore[union-attr]
                key = syn.lower()
                if key not in seen and len(added) < self.max_added_terms:
                    added.append(syn)
                    seen.add(key)
        if not added:
            return ExpansionResult(query=query, expanded_terms=())
        new_text = query.text + " " + " ".join(added)
        return ExpansionResult(
            query=query.with_text(new_text),
            expanded_terms=tuple(added),
            note="synonym_expansion",
        )


@dataclass
class CompositeExpander:
    """Run several expanders in sequence, accumulating added terms."""

    expanders: Sequence[QueryExpander]

    def expand(self, query: RetrievalQuery) -> ExpansionResult:
        current = query
        all_terms: List[str] = []
        degraded = False
        notes: List[str] = []
        for exp in self.expanders:
            res = exp.expand(current)
            current = res.query
            all_terms.extend(res.expanded_terms)
            degraded = degraded or res.degraded
            if res.note:
                notes.append(res.note)
        return ExpansionResult(
            query=current,
            expanded_terms=tuple(all_terms),
            degraded=degraded,
            note=";".join(notes),
        )


def safe_expand(expander: QueryExpander, query: RetrievalQuery) -> ExpansionResult:
    """
    Run an expander defensively.

    On any exception the original query is returned with ``degraded=True`` so
    the caller can record the fallback in diagnostics/metrics without failing
    the whole retrieval. The underlying error is wrapped (not swallowed) for
    log surfaces that want it.
    """
    try:
        result = expander.expand(query)
    except QueryExpansionError:
        return ExpansionResult(
            query=query, degraded=True, note="query_expansion_failed"
        )
    except Exception as exc:  # defensive: never break retrieval on expansion
        return ExpansionResult(
            query=query,
            degraded=True,
            note=f"query_expansion_failed:{type(exc).__name__}",
        )
    return result
