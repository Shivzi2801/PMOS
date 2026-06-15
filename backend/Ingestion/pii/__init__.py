"""PII detection layer.

Detects email, phone, SSN, credit card, API keys and access tokens. Produces
findings with confidence and severity, and supports redact / annotate modes.
"""

from .detectors import (
    Detector,
    EmailDetector,
    PhoneDetector,
    SSNDetector,
    CreditCardDetector,
    APIKeyDetector,
    AccessTokenDetector,
    DEFAULT_DETECTORS,
)
from .engine import PIIEngine, PIIResult

__all__ = [
    "Detector",
    "EmailDetector",
    "PhoneDetector",
    "SSNDetector",
    "CreditCardDetector",
    "APIKeyDetector",
    "AccessTokenDetector",
    "DEFAULT_DETECTORS",
    "PIIEngine",
    "PIIResult",
]
