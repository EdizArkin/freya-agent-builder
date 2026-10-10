"""Versioned policy envelopes. No policy evaluation or executable payloads."""

from dataclasses import dataclass
from enum import StrEnum


SCHEMA_VERSION = "policy-ir/1"


class RuleCategory(StrEnum):
    GUARD = "guard"
    OBLIGATION = "obligation"
    PROHIBITION = "prohibition"
    CALCULATION = "calculation"
    ORDERING = "ordering"
    OUTCOME = "outcome"


class RuleScope(StrEnum):
    CALL = "call"
    PRODUCT = "product"
    QUESTION = "question"
    ACTION = "action"


class RuleStatus(StrEnum):
    CANDIDATE = "candidate"
    UNRESOLVED = "unresolved"
    SUPERSEDED = "superseded"


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class SourceDocument:
    id: str
    sha256: str
    input_order: int
    document_kind: str


@dataclass(frozen=True)
class SourceRef:
    document_id: str
    section: str
    start_offset: int
    end_offset: int
    span_sha256: str


@dataclass(frozen=True)
class BooleanLiteral:
    value: bool
    op: str = "literal"


@dataclass(frozen=True)
class Rule:
    id: str
    category: RuleCategory
    scope: RuleScope
    status: RuleStatus
    source_refs: tuple[SourceRef, ...]
    condition: BooleanLiteral | None = None
    # This milestone accepts only an empty effect sequence.
    effects: tuple[()] = ()


@dataclass(frozen=True)
class PolicyIR:
    schema_version: str
    documents: tuple[SourceDocument, ...]
    rules: tuple[Rule, ...]


@dataclass(frozen=True)
class ValidationFinding:
    code: str
    location: str
    message: str
    severity: Severity = Severity.ERROR


@dataclass(frozen=True)
class ValidationReport:
    findings: tuple[ValidationFinding, ...]
    provenance_checked: bool

    @property
    def valid(self) -> bool:
        """Validity concerns only this milestone's structural/provenance profile."""
        return not any(f.severity == Severity.ERROR for f in self.findings)

    @property
    def compilable(self) -> bool:
        return False

    def to_dict(self) -> dict:
        return {"valid": self.valid, "compilable": False,
                "profile": "policy_ir_foundation", "provenance_checked": self.provenance_checked,
                "semantic_validation": "not_implemented", "capability_checks": "not_implemented",
                "findings": [{"code": f.code, "location": f.location,
                              "message": f.message, "severity": f.severity.value} for f in self.findings]}
