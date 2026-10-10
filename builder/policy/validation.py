"""Conservative shape and byte-backed provenance checks; no semantic approval."""

from collections.abc import Mapping
import hashlib
import re

from builder.policy.models import (PolicyIR, RuleCategory, RuleScope, RuleStatus,
                                   ValidationFinding, ValidationReport)


def validate_data(data) -> ValidationReport:
    """Inspect JSON data without evaluating expressions or echoing source values."""
    findings = []

    def issue(code, location, message):
        findings.append(ValidationFinding(code, location, message))

    def shape(value, required, optional, location):
        if not isinstance(value, dict):
            issue("object_type", location, "Expected an object.")
            return False
        if set(required) - value.keys():
            issue("required_fields", location, "Required fields are missing; consult the IR profile.")
        if value.keys() - set(required) - set(optional):
            issue("unknown_fields", location, "Unknown fields are unsupported; consult the IR profile.")
        return True

    def string(value, location):
        if not isinstance(value, str) or not value.strip():
            issue("string_type", location, "Expected a nonempty string.")
            return False
        return True

    def digest(value, location):
        if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
            issue("hash_format", location, "Expected a lowercase SHA-256 hex digest.")

    def integer(value, location):
        if type(value) is not int or value < 0:
            issue("offset_or_order_type", location, "Expected a nonnegative integer; booleans are not integers.")
            return False
        return True

    def array(value, location):
        if not isinstance(value, list):
            issue("array_type", location, "Expected an array.")
            return []
        return value

    if not shape(data, ("schema_version", "documents", "rules"), (), "$"):
        return ValidationReport(tuple(findings), False)
    if data.get("schema_version") != "policy-ir/1":
        issue("schema_version", "$.schema_version", "Only policy-ir/1 is supported.")
    documents = array(data.get("documents"), "$.documents")
    if not documents:
        issue("documents_empty", "$.documents", "At least one source document is required.")
    doc_ids, orders = set(), set()
    for index, doc in enumerate(documents):
        loc = f"$.documents[{index}]"
        if not shape(doc, ("id", "sha256", "input_order", "document_kind"), (), loc):
            continue
        if string(doc.get("id"), loc + ".id"):
            if doc["id"] in doc_ids:
                issue("duplicate_document_id", loc, "Document identifiers must be unique.")
            doc_ids.add(doc["id"])
        digest(doc.get("sha256"), loc + ".sha256")
        string(doc.get("document_kind"), loc + ".document_kind")
        if integer(doc.get("input_order"), loc + ".input_order"):
            if doc["input_order"] in orders:
                issue("duplicate_input_order", loc, "Document input orders must be unique.")
            orders.add(doc["input_order"])
    rule_ids = set()
    for index, rule in enumerate(array(data.get("rules"), "$.rules")):
        loc = f"$.rules[{index}]"
        if not shape(rule, ("id", "category", "scope", "status", "source_refs"), ("condition", "effects"), loc):
            continue
        if string(rule.get("id"), loc + ".id"):
            if rule["id"] in rule_ids:
                issue("duplicate_rule_id", loc, "Rule identifiers must be unique.")
            rule_ids.add(rule["id"])
        for field, enum_type in (("category", RuleCategory), ("scope", RuleScope), ("status", RuleStatus)):
            if not isinstance(rule.get(field), str) or rule[field] not in {e.value for e in enum_type}:
                issue("enum_value", loc + "." + field, "Unsupported vocabulary value; consult the IR profile.")
        refs = array(rule.get("source_refs"), loc + ".source_refs")
        if not refs:
            issue("missing_provenance", loc, "Every rule requires at least one valid source reference.")
        for number, ref in enumerate(refs):
            rloc = f"{loc}.source_refs[{number}]"
            if not shape(ref, ("document_id", "section", "start_offset", "end_offset", "span_sha256"), (), rloc):
                continue
            if string(ref.get("document_id"), rloc + ".document_id") and ref["document_id"] not in doc_ids:
                issue("unresolved_document", rloc, "Source reference does not resolve to a declared document.")
            string(ref.get("section"), rloc + ".section")
            start = integer(ref.get("start_offset"), rloc + ".start_offset")
            end = integer(ref.get("end_offset"), rloc + ".end_offset")
            if start and end and ref["end_offset"] <= ref["start_offset"]:
                issue("span_range", rloc, "Source span must be nonempty with start less than end.")
            digest(ref.get("span_sha256"), rloc + ".span_sha256")
        condition = rule.get("condition")
        if condition is not None:
            if shape(condition, ("op", "value"), (), loc + ".condition"):
                if condition.get("op") != "literal":
                    issue("unsupported_operator", loc + ".condition", "Only a boolean literal condition is supported; executable expressions are rejected.")
                if type(condition.get("value")) is not bool:
                    issue("condition_value", loc + ".condition", "Literal condition value must be boolean.")
        effects = rule.get("effects", [])
        if not isinstance(effects, list):
            issue("effects_type", loc + ".effects", "Effects must be an empty array in this milestone.")
        elif effects:
            issue("unsupported_effects", loc + ".effects", "Effect execution is unsupported; keep effects empty.")
    return ValidationReport(tuple(findings), False)


def validate_policy(policy: PolicyIR, sources: Mapping[str, bytes]) -> ValidationReport:
    """Verify original UTF-8 bytes and half-open Unicode code-point spans.

    No newline, Unicode, or BOM normalization is performed. Section labels are
    descriptive; this check does not establish that a span supports a rule.
    """
    if not isinstance(policy, PolicyIR):
        return ValidationReport((ValidationFinding("policy_type", "$", "Expected a PolicyIR instance."),), False)
    try:
        # Use the strict codec also for directly constructed dataclasses.
        from builder.policy.codec import dumps, loads, IRValidationError
        policy = loads(dumps(policy))
    except (IRValidationError, TypeError, ValueError, RecursionError):
        return ValidationReport((ValidationFinding("policy_structure", "$", "Policy model does not satisfy the structural profile."),), False)
    if not isinstance(sources, Mapping):
        return ValidationReport((ValidationFinding("sources_type", "$", "Sources must map document IDs to original bytes."),), False)
    findings, texts = [], {}
    for index, doc in enumerate(policy.documents):
        loc = f"$.documents[{index}]"
        raw = sources.get(doc.id)
        if not isinstance(raw, bytes):
            findings.append(ValidationFinding("source_missing", loc, "Original document bytes are required for provenance verification."))
            continue
        if hashlib.sha256(raw).hexdigest() != doc.sha256:
            findings.append(ValidationFinding("document_hash_mismatch", loc, "Supplied document does not match its declared hash."))
        try:
            texts[doc.id] = raw.decode("utf-8")
        except UnicodeError:
            findings.append(ValidationFinding("source_encoding", loc, "This profile requires original UTF-8 document bytes."))
    for index, rule in enumerate(policy.rules):
        for number, ref in enumerate(rule.source_refs):
            loc = f"$.rules[{index}].source_refs[{number}]"
            text = texts.get(ref.document_id)
            if text is None:
                continue
            if ref.end_offset > len(text):
                findings.append(ValidationFinding("span_bounds", loc, "Source span exceeds the supplied document length."))
            elif hashlib.sha256(text[ref.start_offset:ref.end_offset].encode("utf-8")).hexdigest() != ref.span_sha256:
                findings.append(ValidationFinding("span_hash_mismatch", loc, "Source span does not match its declared hash."))
    return ValidationReport(tuple(findings), True)
