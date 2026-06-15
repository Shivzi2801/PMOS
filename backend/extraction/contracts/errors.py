"""Error hierarchy for the F-10 Extraction Engine."""

from __future__ import annotations


class ExtractionError(Exception):
    """Base class for all extraction-engine errors."""


class MalformedDocumentError(ExtractionError):
    """Raised when a CanonicalDocument fails structural validation.

    This is a non-retryable, terminal failure for the affected document. The
    pipeline records ``extraction_failures_total`` and re-raises so the caller
    can route the document to a dead-letter path.
    """


class ExtractorError(ExtractionError):
    """Raised when an individual extractor fails unexpectedly.

    The pipeline isolates extractor failures so a fault in one stage does not
    abort the cascade; the stage is skipped and the failure is counted.
    """


class ConfidenceScoringError(ExtractionError):
    """Raised when an atom cannot be assigned a valid confidence score."""
