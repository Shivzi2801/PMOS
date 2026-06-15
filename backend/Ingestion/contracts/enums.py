"""Enumerations used across the ingestion pipeline contracts."""

from __future__ import annotations

from enum import Enum


class PIIType(str, Enum):
    """Categories of personally identifiable / sensitive information."""

    EMAIL = "EMAIL"
    PHONE = "PHONE"
    SSN = "SSN"
    CREDIT_CARD = "CREDIT_CARD"
    API_KEY = "API_KEY"
    ACCESS_TOKEN = "ACCESS_TOKEN"


class PIISeverity(str, Enum):
    """Severity ranking for a PII finding.

    LOW    - low blast radius if leaked (e.g. business email)
    MEDIUM - personally identifying contact info (e.g. phone)
    HIGH   - regulated / directly damaging (e.g. SSN, credit card)
    CRITICAL - live secrets that grant access (e.g. API keys, tokens)
    """

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"

    @property
    def rank(self) -> int:
        return {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}[self.value]


class RedactionMode(str, Enum):
    """How the PII engine should treat the text it scans."""

    ANNOTATE = "ANNOTATE"  # leave text intact, only report findings
    REDACT = "REDACT"      # replace matched spans with a placeholder token


class InjectionStatus(str, Enum):
    """Outcome of prompt-injection screening."""

    SAFE = "SAFE"
    SUSPECT = "SUSPECT"
    QUARANTINED = "QUARANTINED"

    @property
    def rank(self) -> int:
        return {"SAFE": 0, "SUSPECT": 1, "QUARANTINED": 2}[self.value]


class InjectionCategory(str, Enum):
    """Specific families of prompt-injection technique."""

    INSTRUCTION_OVERRIDE = "INSTRUCTION_OVERRIDE"        # "ignore previous instructions"
    SYSTEM_PROMPT_EXTRACTION = "SYSTEM_PROMPT_EXTRACTION"  # "reveal your system prompt"
    DATA_EXFILTRATION = "DATA_EXFILTRATION"              # "send all data to http://..."
    TOOL_MISUSE = "TOOL_MISUSE"                          # "call the delete tool"
    HIDDEN_PROMPT = "HIDDEN_PROMPT"                      # zero-width / hidden markup payloads
