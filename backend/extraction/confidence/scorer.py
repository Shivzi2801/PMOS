"""Confidence scoring engine.

Each extractor declares the *method* that produced an atom. The
``ConfidenceScorer`` maps that method to a calibrated confidence band and
validates that any extractor-supplied score falls inside the allowed range.

Bands (per Slice 1.3 specification):
  - Rule based : 0.90 .. 1.00
  - Heuristic  : 0.60 .. 0.80
  - Future LLM : 0.70 .. 0.95
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional, Tuple

from ..contracts.atoms import Atom
from ..contracts.errors import ConfidenceScoringError


class ExtractionMethod(str, Enum):
    """How an atom was produced. Drives confidence banding."""

    RULE_BASED = "rule_based"
    HEURISTIC = "heuristic"
    LLM = "llm"


@dataclass(frozen=True)
class ConfidenceBand:
    floor: float
    ceiling: float
    default: float

    def clamp(self, value: float) -> float:
        return max(self.floor, min(self.ceiling, value))

    def contains(self, value: float) -> bool:
        return self.floor <= value <= self.ceiling


# Calibrated bands. ``default`` is used when an extractor provides no signal-
# specific score of its own.
_BANDS: Dict[ExtractionMethod, ConfidenceBand] = {
    ExtractionMethod.RULE_BASED: ConfidenceBand(0.90, 1.00, 0.92),
    ExtractionMethod.HEURISTIC: ConfidenceBand(0.60, 0.80, 0.65),
    ExtractionMethod.LLM: ConfidenceBand(0.70, 0.95, 0.80),
}


class ConfidenceScorer:
    """Assigns and validates confidence scores for extracted atoms."""

    def __init__(self, bands: Optional[Dict[ExtractionMethod, ConfidenceBand]] = None):
        self._bands = bands or dict(_BANDS)

    def band_for(self, method: ExtractionMethod) -> ConfidenceBand:
        try:
            return self._bands[method]
        except KeyError as exc:
            raise ConfidenceScoringError(
                f"No confidence band registered for method '{method}'"
            ) from exc

    def score(
        self,
        method: ExtractionMethod,
        raw_signal: Optional[float] = None,
    ) -> float:
        """Compute a confidence value for the given method.

        ``raw_signal`` is an optional extractor-provided score in [0, 1]. When
        present it is clamped into the method's band; otherwise the band
        default is returned.
        """
        band = self.band_for(method)
        if raw_signal is None:
            return band.default
        if not 0.0 <= raw_signal <= 1.0:
            raise ConfidenceScoringError(
                f"raw_signal must be in [0,1]; got {raw_signal}"
            )
        return band.clamp(raw_signal)

    def apply(self, atom: Atom, method: ExtractionMethod) -> Atom:
        """Validate and, if needed, normalize an atom's confidence in place.

        If the extractor already set a confidence inside the method's band it
        is preserved. If it is out of band it is clamped (defensive — extractor
        bugs must not crash the pipeline). Returns the same atom for chaining.
        """
        band = self.band_for(method)
        if atom.confidence is None:  # type: ignore[comparison-overlap]
            atom.confidence = band.default
        elif not band.contains(atom.confidence):
            atom.confidence = band.clamp(atom.confidence)
        return atom

    def bounds(self, method: ExtractionMethod) -> Tuple[float, float]:
        band = self.band_for(method)
        return (band.floor, band.ceiling)
