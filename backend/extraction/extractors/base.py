"""Base extractor contract.

Every stage of the cheap-first cascade implements ``Extractor``. The pipeline
treats stages uniformly: each receives the document text plus context and
returns zero or more atoms tagged with the method that produced them. This
keeps the cascade open for extension (EntityAtom/RelationshipAtom/etc.) without
changing the pipeline.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import List

from ..confidence.scorer import ExtractionMethod
from ..contracts.atoms import Atom
from ..contracts.results import ExtractionContext


@dataclass
class ExtractorOutput:
    """An extractor's atoms plus the method that produced them."""

    method: ExtractionMethod
    atoms: List[Atom]


class Extractor(abc.ABC):
    """Abstract cascade stage."""

    #: Method tag applied to every atom this extractor emits.
    method: ExtractionMethod

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Stable identifier used in metrics and logs."""

    @abc.abstractmethod
    def extract(self, text: str, context: ExtractionContext) -> ExtractorOutput:
        """Produce atoms from ``text``.

        Implementations MUST be pure and side-effect free, MUST NOT perform I/O
        (no network, no DB) in Slice 1.3, and MUST tolerate empty input by
        returning an empty atom list rather than raising.
        """
