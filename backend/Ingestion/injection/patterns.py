"""Pattern catalogue for prompt-injection screening.

Each pattern is a compiled regex with a category and a weight. Weights sum into
a risk score; the screener maps the score to a status. Weights are tuned so a
single strong signal (e.g. an explicit instruction override) reaches SUSPECT,
and two or more strong signals reach QUARANTINED.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Pattern

from ..contracts import InjectionCategory


@dataclass(frozen=True)
class InjectionPattern:
    name: str
    category: InjectionCategory
    regex: Pattern[str]
    weight: float


def _c(pattern: str) -> Pattern[str]:
    return re.compile(pattern, re.IGNORECASE)


DEFAULT_PATTERNS: List[InjectionPattern] = [
    # --- Instruction override -------------------------------------------------
    InjectionPattern(
        "ignore_previous",
        InjectionCategory.INSTRUCTION_OVERRIDE,
        _c(r"\bignore\s+(?:all\s+)?(?:the\s+)?(?:previous|prior|above|earlier)\s+"
           r"(?:instructions?|prompts?|messages?|rules?)"),
        0.6,
    ),
    InjectionPattern(
        "disregard_instructions",
        InjectionCategory.INSTRUCTION_OVERRIDE,
        _c(r"\b(?:disregard|forget|override|bypass)\s+(?:all\s+)?(?:your\s+)?"
           r"(?:previous\s+|prior\s+|the\s+)?(?:instructions?|rules?|guidelines?|"
           r"directives?|system\s+prompt)"),
        0.6,
    ),
    InjectionPattern(
        "new_instructions",
        InjectionCategory.INSTRUCTION_OVERRIDE,
        _c(r"\b(?:here\s+are\s+your\s+new|follow\s+these\s+new|your\s+new)\s+"
           r"instructions?\b"),
        0.45,
    ),
    InjectionPattern(
        "you_are_now",
        InjectionCategory.INSTRUCTION_OVERRIDE,
        _c(r"\byou\s+are\s+now\s+(?:a|an|in|no\s+longer)\b"),
        0.35,
    ),
    InjectionPattern(
        "developer_mode",
        InjectionCategory.INSTRUCTION_OVERRIDE,
        _c(r"\b(?:developer|dev|jailbreak|dan|god)\s+mode\b"),
        0.4,
    ),

    # --- System prompt extraction --------------------------------------------
    InjectionPattern(
        "reveal_system_prompt",
        InjectionCategory.SYSTEM_PROMPT_EXTRACTION,
        _c(r"\b(?:reveal|show|print|repeat|reproduce|output|tell\s+me)\s+"
           r"(?:your|the)\s+(?:system\s+prompt|initial\s+instructions?|"
           r"original\s+instructions?|prompt)\b"),
        0.6,
    ),
    InjectionPattern(
        "what_are_your_instructions",
        InjectionCategory.SYSTEM_PROMPT_EXTRACTION,
        _c(r"\bwhat\s+(?:are|were)\s+your\s+(?:original\s+|initial\s+)?"
           r"(?:instructions?|rules?|guidelines?)\b"),
        0.45,
    ),
    InjectionPattern(
        "repeat_above",
        InjectionCategory.SYSTEM_PROMPT_EXTRACTION,
        _c(r"\brepeat\s+(?:everything\s+|the\s+text\s+)?above\b"),
        0.4,
    ),

    # --- Data exfiltration ----------------------------------------------------
    InjectionPattern(
        "send_data_to_url",
        InjectionCategory.DATA_EXFILTRATION,
        _c(r"\b(?:send|post|exfiltrate|upload|forward|transmit|leak)\b[^.\n]{0,40}"
           r"\b(?:to|at)\b[^.\n]{0,20}https?://"),
        0.6,
    ),
    InjectionPattern(
        "email_data_out",
        InjectionCategory.DATA_EXFILTRATION,
        _c(r"\b(?:email|send|forward)\b[^.\n]{0,40}\b(?:all\s+)?"
           r"(?:data|records?|credentials?|secrets?|conversation|contents?)\b"
           r"[^.\n]{0,30}\b(?:to)\b[^.\n]{0,30}@"),
        0.6,
    ),
    InjectionPattern(
        "make_http_request",
        InjectionCategory.DATA_EXFILTRATION,
        _c(r"\b(?:make|issue|perform|fetch)\s+(?:a\s+)?(?:get|post|http)\s+"
           r"request\b"),
        0.4,
    ),

    # --- Tool misuse ----------------------------------------------------------
    InjectionPattern(
        "call_tool",
        InjectionCategory.TOOL_MISUSE,
        _c(r"\b(?:call|invoke|execute|run|use)\s+(?:the\s+)?"
           r"(?:tool|function|api|command|delete|drop|shell|exec)\b"),
        0.4,
    ),
    InjectionPattern(
        "delete_or_drop",
        InjectionCategory.TOOL_MISUSE,
        _c(r"\b(?:delete|drop|truncate|wipe|erase|rm\s+-rf)\b[^.\n]{0,30}"
           r"\b(?:all|database|table|records?|files?|everything)\b"),
        0.5,
    ),
    InjectionPattern(
        "grant_permissions",
        InjectionCategory.TOOL_MISUSE,
        _c(r"\b(?:grant|escalate|elevate|give)\b[^.\n]{0,20}"
           r"\b(?:admin|root|privileges?|permissions?|access)\b"),
        0.45,
    ),

    # --- Hidden prompt --------------------------------------------------------
    InjectionPattern(
        "fake_role_marker",
        InjectionCategory.HIDDEN_PROMPT,
        _c(r"(?:^|\n)\s*(?:system|assistant|user)\s*[:>\]]"),
        0.35,
    ),
    InjectionPattern(
        "im_start_marker",
        InjectionCategory.HIDDEN_PROMPT,
        _c(r"<\|?(?:im_start|im_end|endoftext|system)\|?>"),
        0.5,
    ),
    InjectionPattern(
        "instruction_tags",
        InjectionCategory.HIDDEN_PROMPT,
        _c(r"</?(?:system|instructions?|prompt|admin)>"),
        0.4,
    ),
]
