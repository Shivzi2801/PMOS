"""Individual PII / secret detectors.

Each detector yields PIIFinding objects for a given text. Detectors combine
regular expressions with light validation (Luhn check for cards, SSN range
rules, etc.) to raise confidence and suppress obvious false positives.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import Iterable, List

from ..contracts import PIIFinding, PIIType, PIISeverity


def _preview(value: str, keep: int = 4) -> str:
    """Mask a matched value, keeping only a few leading characters."""
    value = value.strip()
    if len(value) <= keep:
        return "*" * len(value)
    return value[:keep] + "*" * (len(value) - keep)


class Detector(ABC):
    name: str = ""
    pii_type: PIIType
    severity: PIISeverity

    @abstractmethod
    def detect(self, text: str) -> List[PIIFinding]:
        raise NotImplementedError


class EmailDetector(Detector):
    name = "email"
    pii_type = PIIType.EMAIL
    severity = PIISeverity.LOW

    _RE = re.compile(
        r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"
    )

    def detect(self, text: str) -> List[PIIFinding]:
        out: List[PIIFinding] = []
        for m in self._RE.finditer(text):
            out.append(
                PIIFinding(
                    pii_type=self.pii_type,
                    severity=self.severity,
                    confidence=0.97,
                    start=m.start(),
                    end=m.end(),
                    preview=_preview(m.group(0)),
                    detector=self.name,
                )
            )
        return out


class PhoneDetector(Detector):
    name = "phone"
    pii_type = PIIType.PHONE
    severity = PIISeverity.MEDIUM

    # International-ish: optional +, separators, 7-15 digits total.
    _RE = re.compile(
        r"(?<![\w.])(\+?\d[\d\s().\-]{6,18}\d)(?![\w])"
    )

    def detect(self, text: str) -> List[PIIFinding]:
        out: List[PIIFinding] = []
        for m in self._RE.finditer(text):
            raw = m.group(1)
            digits = re.sub(r"\D", "", raw)
            if not (7 <= len(digits) <= 15):
                continue
            # Reduce confidence for bare digit runs with no formatting.
            formatted = any(c in raw for c in "+()- ")
            confidence = 0.85 if formatted else 0.6
            out.append(
                PIIFinding(
                    pii_type=self.pii_type,
                    severity=self.severity,
                    confidence=confidence,
                    start=m.start(1),
                    end=m.end(1),
                    preview=_preview(raw, keep=2),
                    detector=self.name,
                )
            )
        return out


class SSNDetector(Detector):
    name = "ssn"
    pii_type = PIIType.SSN
    severity = PIISeverity.HIGH

    _RE = re.compile(r"\b(\d{3})-(\d{2})-(\d{4})\b")

    def detect(self, text: str) -> List[PIIFinding]:
        out: List[PIIFinding] = []
        for m in self._RE.finditer(text):
            area, group, serial = m.group(1), m.group(2), m.group(3)
            # Invalid SSN ranges per SSA rules.
            if area in ("000", "666") or area.startswith("9"):
                continue
            if group == "00" or serial == "0000":
                continue
            out.append(
                PIIFinding(
                    pii_type=self.pii_type,
                    severity=self.severity,
                    confidence=0.9,
                    start=m.start(),
                    end=m.end(),
                    preview="***-**-" + serial,
                    detector=self.name,
                )
            )
        return out


def _luhn_ok(digits: str) -> bool:
    total = 0
    reverse = digits[::-1]
    for i, ch in enumerate(reverse):
        d = ord(ch) - 48
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


class CreditCardDetector(Detector):
    name = "credit_card"
    pii_type = PIIType.CREDIT_CARD
    severity = PIISeverity.HIGH

    _RE = re.compile(r"\b(?:\d[ -]?){13,19}\b")

    def detect(self, text: str) -> List[PIIFinding]:
        out: List[PIIFinding] = []
        for m in self._RE.finditer(text):
            raw = m.group(0)
            digits = re.sub(r"\D", "", raw)
            if not (13 <= len(digits) <= 19):
                continue
            if not _luhn_ok(digits):
                continue
            out.append(
                PIIFinding(
                    pii_type=self.pii_type,
                    severity=self.severity,
                    confidence=0.95,
                    start=m.start(),
                    end=m.end(),
                    preview="**** **** **** " + digits[-4:],
                    detector=self.name,
                )
            )
        return out


class APIKeyDetector(Detector):
    name = "api_key"
    pii_type = PIIType.API_KEY
    severity = PIISeverity.CRITICAL

    # Common vendor key prefixes + generic high-entropy patterns.
    _PATTERNS = [
        # AWS access key id
        (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), 0.98),
        # Google API key
        (re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"), 0.97),
        # Anthropic
        (re.compile(r"\bsk-ant-[0-9A-Za-z\-]{20,}\b"), 0.98),
        # OpenAI / generic sk-
        (re.compile(r"\bsk-[0-9A-Za-z]{20,}\b"), 0.9),
        # Stripe
        (re.compile(r"\b(?:sk|pk|rk)_(?:live|test)_[0-9A-Za-z]{16,}\b"), 0.97),
        # Generic "api_key=..." assignments
        (
            re.compile(
                r"(?i)\b(?:api[_-]?key|apikey|secret[_-]?key)\b\s*[:=]\s*['\"]?"
                r"([0-9A-Za-z_\-]{16,})"
            ),
            0.85,
        ),
    ]

    def detect(self, text: str) -> List[PIIFinding]:
        out: List[PIIFinding] = []
        for pattern, conf in self._PATTERNS:
            for m in pattern.finditer(text):
                # Use captured group if present (assignment form), else whole match.
                grp = m.group(1) if m.groups() else m.group(0)
                start = m.start(1) if m.groups() else m.start(0)
                end = m.end(1) if m.groups() else m.end(0)
                out.append(
                    PIIFinding(
                        pii_type=self.pii_type,
                        severity=self.severity,
                        confidence=conf,
                        start=start,
                        end=end,
                        preview=_preview(grp, keep=4),
                        detector=self.name,
                    )
                )
        return _dedupe_spans(out)


class AccessTokenDetector(Detector):
    name = "access_token"
    pii_type = PIIType.ACCESS_TOKEN
    severity = PIISeverity.CRITICAL

    _PATTERNS = [
        # JWT
        (re.compile(r"\beyJ[0-9A-Za-z_\-]+\.[0-9A-Za-z_\-]+\.[0-9A-Za-z_\-]+\b"), 0.96),
        # GitHub tokens
        (re.compile(r"\bgh[pousr]_[0-9A-Za-z]{36,}\b"), 0.98),
        # Slack tokens
        (re.compile(r"\bxox[baprs]-[0-9A-Za-z\-]{10,}\b"), 0.97),
        # Bearer header
        (re.compile(r"(?i)\bbearer\s+([0-9A-Za-z_\-\.]{16,})"), 0.85),
        # Generic access_token= assignments
        (
            re.compile(
                r"(?i)\b(?:access[_-]?token|auth[_-]?token|token)\b\s*[:=]\s*['\"]?"
                r"([0-9A-Za-z_\-\.]{16,})"
            ),
            0.8,
        ),
    ]

    def detect(self, text: str) -> List[PIIFinding]:
        out: List[PIIFinding] = []
        for pattern, conf in self._PATTERNS:
            for m in pattern.finditer(text):
                grp = m.group(1) if m.groups() else m.group(0)
                start = m.start(1) if m.groups() else m.start(0)
                end = m.end(1) if m.groups() else m.end(0)
                out.append(
                    PIIFinding(
                        pii_type=self.pii_type,
                        severity=self.severity,
                        confidence=conf,
                        start=start,
                        end=end,
                        preview=_preview(grp, keep=4),
                        detector=self.name,
                    )
                )
        return _dedupe_spans(out)


def _dedupe_spans(findings: List[PIIFinding]) -> List[PIIFinding]:
    """Keep the highest-confidence finding when spans overlap."""
    findings = sorted(findings, key=lambda f: (f.start, -f.confidence))
    kept: List[PIIFinding] = []
    for f in findings:
        overlap = next(
            (k for k in kept if not (f.end <= k.start or f.start >= k.end)), None
        )
        if overlap is None:
            kept.append(f)
        elif f.confidence > overlap.confidence:
            kept.remove(overlap)
            kept.append(f)
    return sorted(kept, key=lambda f: f.start)


DEFAULT_DETECTORS: List[Detector] = [
    EmailDetector(),
    PhoneDetector(),
    SSNDetector(),
    CreditCardDetector(),
    APIKeyDetector(),
    AccessTokenDetector(),
]
