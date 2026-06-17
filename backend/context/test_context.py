"""Unit tests for the Context Assembly layer (S1.7).

Run with: python -m pytest backend/context/test_context.py
or:        python -m unittest backend.context.test_context
"""

from __future__ import annotations

import unittest

from .citation_record import CitationRecord
from .context_assembler import ContextAssembler
from .context_filters import ACLFilter, DeduplicationFilter
from .context_package import (
    ContextPackage,
    RetrievalResult,
    RetrievedChunk,
)
from .context_ranker import ContextRanker
from .errors import (
    EmptyContextError,
    InvalidRetrievalResultError,
    PromptTemplateError,
    TokenBudgetError,
)
from .metrics import AssemblyMetrics
from .prompt_builder import PromptBuilder, PromptMessage
from .token_budget import TokenBudget, heuristic_token_counter


def chunk(cid, content="content", score=1.0, doc="d", acl=(), **kw):
    return RetrievedChunk(
        chunk_id=cid,
        document_id=doc,
        content=content,
        score=score,
        acl_tags=tuple(acl),
        **kw,
    )


def words_counter(text):
    """Deterministic 1-token-per-word counter for predictable budget tests."""
    return len(text.split())


# ---------------------------------------------------------------------------
# TokenBudget
# ---------------------------------------------------------------------------
class TestTokenBudget(unittest.TestCase):
    def test_available_for_context(self):
        b = TokenBudget(
            max_context_window=100,
            reserved_system=10,
            reserved_query=5,
            reserved_citations=5,
            reserved_response=20,
        )
        self.assertEqual(b.total_reserved, 40)
        self.assertEqual(b.available_for_context, 60)

    def test_rejects_nonpositive_window(self):
        with self.assertRaises(TokenBudgetError):
            TokenBudget(max_context_window=0)

    def test_rejects_negative_reservation(self):
        with self.assertRaises(TokenBudgetError):
            TokenBudget(max_context_window=100, reserved_system=-1)

    def test_rejects_over_reservation(self):
        with self.assertRaises(TokenBudgetError):
            TokenBudget(max_context_window=10, reserved_response=10)

    def test_heuristic_counter(self):
        self.assertEqual(heuristic_token_counter(""), 0)
        self.assertGreaterEqual(heuristic_token_counter("hello world"), 2)

    def test_fits(self):
        b = TokenBudget(max_context_window=10, reserved_response=4)
        self.assertTrue(b.fits(6))
        self.assertFalse(b.fits(7))


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------
class TestDeduplicationFilter(unittest.TestCase):
    def test_dedup_by_id(self):
        f = DeduplicationFilter()
        kept, dropped = f.apply(
            [chunk("a", "ca"), chunk("a", "ca"), chunk("b", "cb")]
        )
        self.assertEqual([c.chunk_id for c in kept], ["a", "b"])
        self.assertEqual(dropped, ["a"])

    def test_dedup_by_content_hash(self):
        f = DeduplicationFilter()
        kept, dropped = f.apply(
            [chunk("a", "Same Text"), chunk("b", "same   text")]
        )
        self.assertEqual([c.chunk_id for c in kept], ["a"])
        self.assertEqual(dropped, ["b"])

    def test_keeps_first_occurrence(self):
        f = DeduplicationFilter()
        kept, _ = f.apply([chunk("a", "x"), chunk("a", "y")])
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].content, "x")


class TestACLFilter(unittest.TestCase):
    def test_public_chunk_visible(self):
        f = ACLFilter.from_iterable([])
        kept, dropped = f.apply([chunk("a")])
        self.assertEqual(len(kept), 1)
        self.assertEqual(dropped, [])

    def test_allowed_tag_visible(self):
        f = ACLFilter.from_iterable(["finance"])
        kept, dropped = f.apply(
            [chunk("a", acl=["finance"]), chunk("b", acl=["hr"])]
        )
        self.assertEqual([c.chunk_id for c in kept], ["a"])
        self.assertEqual(dropped, ["b"])

    def test_any_matching_tag_grants_access(self):
        f = ACLFilter.from_iterable(["hr"])
        kept, _ = f.apply([chunk("a", acl=["finance", "hr"])])
        self.assertEqual(len(kept), 1)


# ---------------------------------------------------------------------------
# Ranker
# ---------------------------------------------------------------------------
class TestContextRanker(unittest.TestCase):
    def test_orders_by_score_descending(self):
        r = ContextRanker()
        ranked = r.rank([chunk("a", score=0.1), chunk("b", score=0.9)])
        self.assertEqual([c.chunk_id for c in ranked], ["b", "a"])

    def test_deterministic_tie_break_on_id(self):
        r = ContextRanker()
        ranked = r.rank(
            [chunk("z", score=0.5), chunk("a", score=0.5), chunk("m", score=0.5)]
        )
        self.assertEqual([c.chunk_id for c in ranked], ["a", "m", "z"])


# ---------------------------------------------------------------------------
# Citation record
# ---------------------------------------------------------------------------
class TestCitationRecord(unittest.TestCase):
    def test_render_with_title_and_uri(self):
        c = CitationRecord(
            marker="[1]", chunk_id="c", document_id="d",
            source_uri="http://x", title="Title",
        )
        self.assertEqual(c.render(), "[1] Title (http://x)")

    def test_render_fallback_to_document_id(self):
        c = CitationRecord(marker="[2]", chunk_id="c", document_id="docX")
        self.assertEqual(c.render(), "[2] docX")


# ---------------------------------------------------------------------------
# Assembler
# ---------------------------------------------------------------------------
class TestContextAssembler(unittest.TestCase):
    def setUp(self):
        self.budget = TokenBudget(
            max_context_window=100,
            reserved_response=10,
            token_counter=words_counter,
        )
        self.assembler = ContextAssembler(budget=self.budget)

    def _result(self, chunks):
        return RetrievalResult(query="what is x?", chunks=chunks)

    def test_validation_empty_query(self):
        with self.assertRaises(InvalidRetrievalResultError):
            self.assembler.assemble(RetrievalResult(query="  ", chunks=[]))

    def test_validation_bad_chunk_type(self):
        with self.assertRaises(InvalidRetrievalResultError):
            self.assembler.assemble(
                RetrievalResult(query="q", chunks=["not a chunk"])  # type: ignore
            )

    def test_assemble_orders_and_marks(self):
        res = self._result(
            [chunk("a", "alpha", 0.2), chunk("b", "beta", 0.8)]
        )
        pkg = self.assembler.assemble(res)
        self.assertIsInstance(pkg, ContextPackage)
        # Highest score first, marker [1].
        self.assertEqual(pkg.chunks[0].chunk_id, "b")
        self.assertEqual(pkg.chunks[0].marker, "[1]")
        self.assertEqual(pkg.citations[0].chunk_id, "b")

    def test_dedup_applied(self):
        res = self._result([chunk("a", "same"), chunk("a", "same")])
        m = AssemblyMetrics()
        pkg = self.assembler.assemble(res, metrics=m)
        self.assertEqual(len(pkg.chunks), 1)
        self.assertEqual(m.duplicate_chunks_removed, 1)

    def test_acl_applied(self):
        res = self._result(
            [chunk("a", "x", acl=["secret"]), chunk("b", "y")]
        )
        acl = ACLFilter.from_iterable([])  # no grants
        m = AssemblyMetrics()
        pkg = self.assembler.assemble(res, acl=acl, metrics=m)
        self.assertEqual([c.chunk_id for c in pkg.chunks], ["b"])
        self.assertEqual(m.acl_filtered_chunks, 1)

    def test_budget_enforcement_greedy_fill(self):
        # available = 100 - 10 = 90 tokens (words).
        big = chunk("big", " ".join(["w"] * 80), score=0.9)
        small = chunk("small", " ".join(["w"] * 5), score=0.8)
        # 'big' first (80) fits; 'small' (5) also fits -> 85 total.
        res = self._result([big, small])
        m = AssemblyMetrics()
        pkg = self.assembler.assemble(res, metrics=m)
        self.assertEqual(len(pkg.chunks), 2)
        self.assertEqual(pkg.used_context_tokens, 85)

    def test_budget_drops_overflowing_chunk_but_keeps_later_fit(self):
        too_big = chunk("toobig", " ".join(["w"] * 200), score=0.9)
        fits = chunk("fits", " ".join(["w"] * 10), score=0.8)
        res = self._result([too_big, fits])
        m = AssemblyMetrics()
        pkg = self.assembler.assemble(res, metrics=m)
        self.assertEqual([c.chunk_id for c in pkg.chunks], ["fits"])
        self.assertIn("toobig", pkg.dropped_chunk_ids)
        self.assertEqual(m.budget_dropped_chunks, 1)

    def test_empty_context_raises(self):
        res = self._result([chunk("a", "x", acl=["secret"])])
        acl = ACLFilter.from_iterable([])
        with self.assertRaises(EmptyContextError):
            self.assembler.assemble(res, acl=acl)

    def test_empty_context_allowed(self):
        assembler = ContextAssembler(budget=self.budget, allow_empty_context=True)
        res = self._result([])
        pkg = assembler.assemble(res)
        self.assertTrue(pkg.is_empty)

    def test_metrics_snapshot(self):
        res = self._result([chunk("a", "alpha beta", 0.5)])
        m = AssemblyMetrics()
        self.assembler.assemble(res, metrics=m)
        snap = m.snapshot()
        self.assertEqual(snap["selected_chunks"], 1.0)
        self.assertGreaterEqual(snap["budget_utilization"], 0.0)


# ---------------------------------------------------------------------------
# PromptBuilder + end-to-end
# ---------------------------------------------------------------------------
class TestPromptBuilder(unittest.TestCase):
    def setUp(self):
        self.budget = TokenBudget(
            max_context_window=200,
            reserved_response=20,
            token_counter=words_counter,
        )
        self.assembler = ContextAssembler(budget=self.budget)

    def test_end_to_end_build_prompt(self):
        res = RetrievalResult(
            query="explain photosynthesis",
            chunks=[
                chunk("a", "plants convert light", 0.9, source_uri="u1", title="T1"),
                chunk("b", "chlorophyll absorbs light", 0.7),
            ],
        )
        m = AssemblyMetrics()
        pkg = self.assembler.build_prompt(res, metrics=m)
        self.assertEqual(pkg.template_name, "qa_grounded")
        self.assertEqual(pkg.max_response_tokens, 20)
        self.assertEqual(len(pkg.messages), 2)
        self.assertEqual(pkg.messages[0].role, "system")
        self.assertEqual(pkg.messages[1].role, "user")
        self.assertIn("explain photosynthesis", pkg.messages[1].content)
        self.assertIn("[1]", pkg.messages[1].content)
        self.assertEqual(m.estimated_prompt_tokens, pkg.estimated_prompt_tokens)

    def test_unknown_template_raises(self):
        res = RetrievalResult(query="q", chunks=[chunk("a", "x")])
        with self.assertRaises(PromptTemplateError):
            self.assembler.build_prompt(res, template_name="does_not_exist")

    def test_custom_template_registration(self):
        class TerseTemplate:
            name = "terse"

            def build(self, context, budget):
                return [PromptMessage(role="user", content=context.query)]

        builder = PromptBuilder(budget=self.budget)
        builder.register_template("terse", TerseTemplate)
        self.assertIn("terse", builder.available_templates())

        assembler = ContextAssembler(budget=self.budget, prompt_builder=builder)
        res = RetrievalResult(query="hi", chunks=[chunk("a", "x")])
        pkg = assembler.build_prompt(res, template_name="terse")
        self.assertEqual(pkg.template_name, "terse")
        self.assertEqual(len(pkg.messages), 1)

    def test_to_message_dicts(self):
        res = RetrievalResult(query="q", chunks=[chunk("a", "ctx")])
        pkg = self.assembler.build_prompt(res)
        dicts = pkg.to_message_dicts()
        self.assertEqual(dicts[0]["role"], "system")
        self.assertIn("content", dicts[1])


if __name__ == "__main__":
    unittest.main()
