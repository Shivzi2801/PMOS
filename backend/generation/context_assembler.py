"""Context Assembler — orchestrator of the S1.7 pipeline.

Pipeline order:

  RetrievalResult (S1.6)
    -> validate
    -> deduplicate            (context_filters.DeduplicationFilter)
    -> ACL filter             (context_filters.ACLFilter)
    -> rank                   (context_ranker.ContextRanker)
    -> enforce token budget   (token_budget.TokenBudget)
    -> assign citation markers + build citation records
    -> ContextPackage
    -> PromptBuilder          -> PromptPackage   (Wave 2 input)

The assembler performs NO LLM calls and depends on no framework. It consumes
the S1.6 contract and emits a transport-neutral PromptPackage.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import List, Optional

from .citation_record import CitationRecord
from .context_filters import ACLFilter, DeduplicationFilter
from .context_package import (
    ContextChunk,
    ContextPackage,
    RetrievalResult,
    RetrievedChunk,
)
from .context_ranker import ContextRanker
from .errors import (
    EmptyContextError,
    InvalidRetrievalResultError,
)
from .metrics import AssemblyMetrics
from .prompt_builder import PromptBuilder
from .prompt_package import PromptPackage
from .token_budget import TokenBudget


@dataclass
class ContextAssembler:
    """Assembles retrieval output into a model-ready PromptPackage.

    Attributes:
        budget: Token budget governing context selection and response room.
        ranker: Strategy for ordering chunks by relevance.
        deduplicator: Duplicate-removal filter.
        prompt_builder: Builder that renders the final PromptPackage.
        allow_empty_context: If False (default), raise when no chunks survive.
    """

    budget: TokenBudget
    ranker: ContextRanker = field(default_factory=ContextRanker)
    deduplicator: DeduplicationFilter = field(default_factory=DeduplicationFilter)
    prompt_builder: Optional[PromptBuilder] = None
    allow_empty_context: bool = False

    def __post_init__(self) -> None:
        if self.prompt_builder is None:
            self.prompt_builder = PromptBuilder(budget=self.budget)

    # -- validation ---------------------------------------------------------
    def _validate(self, result: RetrievalResult) -> None:
        if result is None:
            raise InvalidRetrievalResultError("retrieval result is None")
        if not isinstance(result.query, str) or not result.query.strip():
            raise InvalidRetrievalResultError("query must be a non-empty string")
        if result.chunks is None:
            raise InvalidRetrievalResultError("chunks must not be None")
        for chunk in result.chunks:
            if not isinstance(chunk, RetrievedChunk):
                raise InvalidRetrievalResultError(
                    "each chunk must be a RetrievedChunk"
                )
            if not chunk.chunk_id:
                raise InvalidRetrievalResultError("chunk_id must be non-empty")

    # -- budget enforcement -------------------------------------------------
    def _enforce_budget(
        self, ranked: List[RetrievedChunk]
    ) -> tuple[List[ContextChunk], List[CitationRecord], List[str], int]:
        available = self.budget.available_for_context
        selected: List[ContextChunk] = []
        citations: List[CitationRecord] = []
        dropped: List[str] = []
        used = 0
        marker_index = 1
        for chunk in ranked:
            cost = self.budget.count(chunk.content)
            if used + cost > available:
                # Greedy fill: a chunk that does not fit is dropped, but later
                # smaller chunks may still fit, so we continue rather than break.
                dropped.append(chunk.chunk_id)
                continue
            marker = f"[{marker_index}]"
            selected.append(
                ContextChunk(source=chunk, marker=marker, token_cost=cost)
            )
            citations.append(
                CitationRecord(
                    marker=marker,
                    chunk_id=chunk.chunk_id,
                    document_id=chunk.document_id,
                    source_uri=chunk.source_uri,
                    title=chunk.title,
                    score=chunk.score,
                    metadata=dict(chunk.metadata),
                )
            )
            used += cost
            marker_index += 1
        return selected, citations, dropped, used

    # -- public API ---------------------------------------------------------
    def assemble(
        self,
        result: RetrievalResult,
        acl: Optional[ACLFilter] = None,
        metrics: Optional[AssemblyMetrics] = None,
    ) -> ContextPackage:
        """Run filtering, ranking, and budgeting -> ContextPackage."""
        metrics = metrics or AssemblyMetrics()
        start = time.perf_counter()

        self._validate(result)
        chunks = list(result.chunks)
        metrics.input_chunks = len(chunks)
        all_dropped: List[str] = []

        # 1. Deduplicate.
        chunks, dup_dropped = self.deduplicator.apply(chunks)
        metrics.duplicate_chunks_removed = len(dup_dropped)
        all_dropped.extend(dup_dropped)

        # 2. ACL filter.
        if acl is not None:
            chunks, acl_dropped = acl.apply(chunks)
            metrics.acl_filtered_chunks = len(acl_dropped)
            all_dropped.extend(acl_dropped)

        # 3. Rank.
        ranked = self.ranker.rank(chunks)

        # 4. Enforce token budget + assign markers + citations.
        selected, citations, budget_dropped, used = self._enforce_budget(ranked)
        metrics.budget_dropped_chunks = len(budget_dropped)
        all_dropped.extend(budget_dropped)

        metrics.selected_chunks = len(selected)
        metrics.used_context_tokens = used
        metrics.available_context_tokens = self.budget.available_for_context
        metrics.record_timer("assemble", time.perf_counter() - start)

        if not selected and not self.allow_empty_context:
            raise EmptyContextError(
                "no chunks remain after dedup/ACL/budget enforcement"
            )

        return ContextPackage(
            query=result.query,
            chunks=tuple(selected),
            citations=tuple(citations),
            used_context_tokens=used,
            dropped_chunk_ids=tuple(all_dropped),
            metadata=dict(result.metadata),
        )

    def build_prompt(
        self,
        result: RetrievalResult,
        acl: Optional[ACLFilter] = None,
        template_name: Optional[str] = None,
        metrics: Optional[AssemblyMetrics] = None,
    ) -> PromptPackage:
        """End-to-end: RetrievalResult -> PromptPackage."""
        metrics = metrics or AssemblyMetrics()
        context = self.assemble(result, acl=acl, metrics=metrics)
        assert self.prompt_builder is not None  # set in __post_init__
        package = self.prompt_builder.build(context, template_name=template_name)
        metrics.estimated_prompt_tokens = package.estimated_prompt_tokens
        return package
