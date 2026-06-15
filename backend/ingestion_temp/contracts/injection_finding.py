"""The InjectionFinding contract — result of prompt-injection screening."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List

from .enums import InjectionStatus, InjectionCategory


@dataclass
class InjectionMatch:
    """A single pattern hit inside the screened text."""

    category: InjectionCategory
    pattern_name: str
    weight: float                # contribution toward the overall risk score
    start: int
    end: int
    preview: str

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["category"] = self.category.value
        return d


@dataclass
class InjectionFinding:
    """Aggregate verdict from the injection screener for one document."""

    status: InjectionStatus
    score: float                              # cumulative risk score
    matches: List[InjectionMatch] = field(default_factory=list)

    @property
    def categories(self) -> List[InjectionCategory]:
        seen: List[InjectionCategory] = []
        for m in self.matches:
            if m.category not in seen:
                seen.append(m.category)
        return seen

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status.value,
            "score": self.score,
            "categories": [c.value for c in self.categories],
            "matches": [m.to_dict() for m in self.matches],
        }
