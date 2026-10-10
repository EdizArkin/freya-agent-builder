"""Strict deterministic JSON codec for the foundation profile."""

from dataclasses import asdict
import json

from builder.policy.models import (BooleanLiteral, PolicyIR, Rule, RuleCategory,
    RuleScope, RuleStatus, SourceDocument, SourceRef, ValidationFinding, ValidationReport)
from builder.policy.validation import validate_data


class IRValidationError(ValueError):
    def __init__(self, report: ValidationReport):
        self.report = report
        super().__init__("Policy IR failed validation; inspect the sanitized report.")


def loads(text: str) -> PolicyIR:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate key")
            result[key] = value
        return result

    def reject_constant(_):
        raise ValueError("Nonfinite number")

    try:
        if not isinstance(text, str):
            raise TypeError()
        data = json.loads(text, object_pairs_hook=unique, parse_constant=reject_constant)
        # Reject overflow such as 1e999, including values in unsupported fields.
        json.dumps(data, allow_nan=False)
    except (ValueError, TypeError, RecursionError):
        raise IRValidationError(ValidationReport((ValidationFinding(
            "json_parse", "$", "Expected strict finite JSON without duplicate keys."),), False)) from None
    report = validate_data(data)
    if not report.valid:
        raise IRValidationError(report)
    return PolicyIR(data["schema_version"], tuple(SourceDocument(**d) for d in data["documents"]),
        tuple(Rule(r["id"], RuleCategory(r["category"]), RuleScope(r["scope"]), RuleStatus(r["status"]),
            tuple(SourceRef(**ref) for ref in r["source_refs"]),
            BooleanLiteral(**r["condition"]) if r.get("condition") is not None else None,
            ()) for r in data["rules"]))


def dumps(policy: PolicyIR) -> str:
    """Canonical JSON; reject models whose values do not match the profile."""
    if not isinstance(policy, PolicyIR):
        raise TypeError("Expected PolicyIR")
    try:
        data = asdict(policy)
        text = json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    except (ValueError, TypeError, RecursionError):
        raise IRValidationError(ValidationReport((ValidationFinding(
            "model_json", "$", "Model contains unsupported JSON values."),), False)) from None
    # JSON converts dataclass tuples into arrays before structural validation.
    loads(text)
    return text
