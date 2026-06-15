"""Prompt-injection screening layer.

Detects instruction-override, system-prompt extraction, data exfiltration,
tool-misuse, and hidden-prompt attacks. Produces an InjectionFinding with a
status of SAFE | SUSPECT | QUARANTINED.
"""

from .patterns import InjectionPattern, DEFAULT_PATTERNS
from .screener import InjectionScreener

__all__ = [
    "InjectionPattern",
    "DEFAULT_PATTERNS",
    "InjectionScreener",
]
