"""Configuration validation.

The validator enforces structural and semantic correctness of configuration
payloads before they are persisted. It is schema-driven: a schema is a mapping
of key -> :class:`FieldSpec`. Validation is intentionally strict by default
(unknown keys rejected) to prevent silent typos in operational settings.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Optional

from .errors import ConfigurationValidationError


@dataclass
class FieldSpec:
    """Specification for a single configuration field.

    Attributes:
        type: Expected Python type (or tuple of types).
        required: Whether the key must be present.
        default: Value applied when key absent and not required.
        choices: Optional allowed value set.
        validator: Optional predicate returning True if the value is acceptable.
        description: Human documentation.
    """

    type: type | tuple[type, ...] = object
    required: bool = False
    default: Any = None
    choices: Optional[Iterable[Any]] = None
    validator: Optional[Callable[[Any], bool]] = None
    description: str = ""


class ConfigurationValidator:
    """Validates configuration ``settings`` dictionaries against schemas.

    Schemas are registered per scope (``"system"``, ``"tenant"``,
    ``"workspace"``). When no schema is registered for a scope, validation only
    checks that the payload is a JSON-serializable mapping (lenient mode), which
    allows new settings to be introduced before a schema is formalized.
    """

    def __init__(self, *, strict_unknown: bool = True) -> None:
        self._schemas: dict[str, dict[str, FieldSpec]] = {}
        self._strict_unknown = strict_unknown

    def register_schema(self, scope: str, schema: Mapping[str, FieldSpec]) -> None:
        self._schemas[scope] = dict(schema)

    def has_schema(self, scope: str) -> bool:
        return scope in self._schemas

    def validate(self, scope: str, settings: Mapping[str, Any]) -> dict[str, Any]:
        """Validate ``settings`` for ``scope``; return a normalized copy.

        Raises:
            ConfigurationValidationError: if any field is invalid.
        """
        if not isinstance(settings, Mapping):
            raise ConfigurationValidationError(
                "settings must be a mapping",
                details={"scope": scope, "type": type(settings).__name__},
            )

        schema = self._schemas.get(scope)
        if schema is None:
            # Lenient mode: ensure values are serializable primitives/containers.
            self._assert_serializable(settings)
            return dict(settings)

        errors: list[dict[str, Any]] = []
        normalized: dict[str, Any] = {}

        # Unknown keys
        if self._strict_unknown:
            unknown = set(settings) - set(schema)
            for key in sorted(unknown):
                errors.append({"field": key, "error": "unknown_field"})

        for key, spec in schema.items():
            present = key in settings
            if not present:
                if spec.required:
                    errors.append({"field": key, "error": "required"})
                elif spec.default is not None:
                    normalized[key] = spec.default
                continue

            value = settings[key]
            if not isinstance(value, spec.type):
                errors.append(
                    {
                        "field": key,
                        "error": "type",
                        "expected": _type_name(spec.type),
                        "got": type(value).__name__,
                    }
                )
                continue
            if spec.choices is not None and value not in spec.choices:
                errors.append(
                    {"field": key, "error": "choice", "allowed": list(spec.choices)}
                )
                continue
            if spec.validator is not None:
                try:
                    ok = spec.validator(value)
                except Exception as exc:  # validator raised
                    errors.append({"field": key, "error": "validator_raised", "detail": str(exc)})
                    continue
                if not ok:
                    errors.append({"field": key, "error": "validator"})
                    continue
            normalized[key] = value

        if errors:
            raise ConfigurationValidationError(
                f"configuration validation failed for scope '{scope}'",
                details={"scope": scope, "violations": errors},
            )
        return normalized

    @staticmethod
    def _assert_serializable(settings: Mapping[str, Any]) -> None:
        import json

        try:
            json.dumps(settings)
        except (TypeError, ValueError) as exc:
            raise ConfigurationValidationError(
                "settings are not JSON-serializable",
                details={"detail": str(exc)},
            ) from exc


def _type_name(t: type | tuple[type, ...]) -> str:
    if isinstance(t, tuple):
        return "|".join(x.__name__ for x in t)
    return t.__name__
