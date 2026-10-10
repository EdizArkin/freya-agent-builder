"""Synthetic sources and policies only; no toolkit imports or fixtures."""

from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import unittest

from builder.policy.codec import IRValidationError, dumps, loads
from builder.policy.models import (BooleanLiteral, PolicyIR, Rule, RuleCategory, RuleScope,
    RuleStatus, SCHEMA_VERSION, SourceDocument, SourceRef)
from builder.policy.validation import validate_data, validate_policy


def sha(value):
    return hashlib.sha256(value).hexdigest()


def fixture():
    raw = "# Synthetic\r\nRequire a confirmed selection. Café.\r\n".encode("utf-8")
    text = raw.decode("utf-8")
    start, end = text.index("Require"), text.index(" Café")
    doc = SourceDocument("source-1", sha(raw), 0, "synthetic_brief")
    ref = SourceRef(doc.id, "Synthetic", start, end, sha(text[start:end].encode("utf-8")))
    rule = Rule("rule-1", RuleCategory.GUARD, RuleScope.ACTION, RuleStatus.CANDIDATE,
                (ref,), BooleanLiteral(True))
    return PolicyIR(SCHEMA_VERSION, (doc,), (rule,)), {doc.id: raw}


class PolicyIRTests(unittest.TestCase):
    def data(self):
        policy, _ = fixture()
        return json.loads(dumps(policy))

    def assert_code(self, data, code):
        with self.assertRaises(IRValidationError) as caught:
            loads(json.dumps(data))
        self.assertIn(code, {f.code for f in caught.exception.report.findings})
        self.assertFalse(caught.exception.report.compilable)

    def test_valid_provenance_and_noncompilable(self):
        policy, sources = fixture()
        before = deepcopy(sources)
        report = validate_policy(policy, sources)
        self.assertTrue(report.valid)
        self.assertTrue(report.provenance_checked)
        self.assertFalse(report.compilable)
        self.assertEqual(sources, before)
        self.assertEqual(report.to_dict()["semantic_validation"], "not_implemented")
        json.dumps(report.to_dict(), allow_nan=False)

    def test_roundtrip_and_determinism(self):
        policy, _ = fixture()
        text = dumps(policy)
        self.assertEqual(loads(text), policy)
        self.assertEqual(dumps(loads(text)), text)
        reordered = dict(reversed(list(json.loads(text).items())))
        self.assertEqual(dumps(loads(json.dumps(reordered))), text)
        self.assertEqual(loads(text).rules[0].category, RuleCategory.GUARD)

    def test_unicode_offsets_and_exact_newlines(self):
        policy, sources = fixture()
        text = sources["source-1"].decode("utf-8")
        start = text.index("Café")
        ref = SourceRef("source-1", "Synthetic", start, start + 4, sha("Café".encode()))
        policy = replace(policy, rules=(replace(policy.rules[0], source_refs=(ref,)),))
        self.assertTrue(validate_policy(policy, sources).valid)
        altered = {"source-1": sources["source-1"].replace(b'\r\n', b'\n')}
        self.assertFalse(validate_policy(policy, altered).valid)

    def test_missing_source(self):
        policy, _ = fixture()
        report = validate_policy(policy, {})
        self.assertFalse(report.valid)
        self.assertIn("source_missing", {f.code for f in report.findings})

    def test_altered_document(self):
        policy, sources = fixture()
        sources["source-1"] += b"changed"
        report = validate_policy(policy, sources)
        self.assertIn("document_hash_mismatch", {f.code for f in report.findings})

    def test_altered_span(self):
        policy, sources = fixture()
        ref = replace(policy.rules[0].source_refs[0], span_sha256="0" * 64)
        policy = replace(policy, rules=(replace(policy.rules[0], source_refs=(ref,)),))
        self.assertIn("span_hash_mismatch", {f.code for f in validate_policy(policy, sources).findings})

    def test_span_bounds(self):
        policy, sources = fixture()
        ref = replace(policy.rules[0].source_refs[0], end_offset=9999)
        policy = replace(policy, rules=(replace(policy.rules[0], source_refs=(ref,)),))
        self.assertIn("span_bounds", {f.code for f in validate_policy(policy, sources).findings})

    def test_invalid_ranges(self):
        for start, end in ((-1, 2), (2, 2), (3, 2), (True, 4), (1.0, 4)):
            data = self.data()
            ref = data["rules"][0]["source_refs"][0]
            ref.update(start_offset=start, end_offset=end)
            with self.subTest(start=start, end=end), self.assertRaises(IRValidationError):
                loads(json.dumps(data))

    def test_missing_provenance(self):
        data = self.data()
        data["rules"][0]["source_refs"] = []
        self.assert_code(data, "missing_provenance")
        del data["rules"][0]["source_refs"]
        self.assert_code(data, "required_fields")

    def test_unresolved_document(self):
        data = self.data()
        data["rules"][0]["source_refs"][0]["document_id"] = "absent"
        self.assert_code(data, "unresolved_document")

    def test_duplicate_ids_and_orders(self):
        for field, code in (("documents", "duplicate_document_id"), ("rules", "duplicate_rule_id")):
            data = self.data()
            data[field].append(deepcopy(data[field][0]))
            self.assert_code(data, code)
        data = self.data()
        doc = deepcopy(data["documents"][0])
        doc["id"] = "source-2"
        data["documents"].append(doc)
        self.assert_code(data, "duplicate_input_order")

    def test_unknown_fields_at_each_level(self):
        for scope in ("root", "document", "rule", "ref", "condition"):
            data = self.data()
            targets = {"root": data, "document": data["documents"][0], "rule": data["rules"][0],
                       "ref": data["rules"][0]["source_refs"][0], "condition": data["rules"][0]["condition"]}
            targets[scope]["private_marker"] = "hidden"
            self.assert_code(data, "unknown_fields")

    def test_required_fields_at_each_level(self):
        for scope, field in (("root", "rules"), ("document", "sha256"), ("rule", "status"),
                             ("ref", "section"), ("condition", "value")):
            data = self.data()
            targets = {"root": data, "document": data["documents"][0], "rule": data["rules"][0],
                       "ref": data["rules"][0]["source_refs"][0], "condition": data["rules"][0]["condition"]}
            del targets[scope][field]
            self.assert_code(data, "required_fields")

    def test_unsupported_version_and_vocabulary(self):
        data = self.data()
        data["schema_version"] = "policy-ir/999"
        self.assert_code(data, "schema_version")
        for field in ("category", "scope", "status"):
            data = self.data()
            data["rules"][0][field] = "unsupported"
            self.assert_code(data, "enum_value")

    def test_unsupported_expressions_and_effects(self):
        data = self.data()
        data["rules"][0]["condition"]["op"] = "eval"
        self.assert_code(data, "unsupported_operator")
        data = self.data()
        data["rules"][0]["effects"] = [{"kind": "execute", "code": "private_marker"}]
        self.assert_code(data, "unsupported_effects")
        data["rules"][0]["effects"] = None
        self.assert_code(data, "effects_type")

    def test_optional_payloads_canonicalized(self):
        data = self.data()
        del data["rules"][0]["condition"]
        del data["rules"][0]["effects"]
        result = loads(json.dumps(data))
        self.assertIsNone(result.rules[0].condition)
        self.assertEqual(result.rules[0].effects, ())
        self.assertEqual(loads(dumps(result)), result)

    def test_types_and_hash_formats(self):
        for field, value in (("id", ""), ("input_order", True), ("sha256", "bad"), ("document_kind", [])):
            data = self.data()
            data["documents"][0][field] = value
            with self.assertRaises(IRValidationError):
                loads(json.dumps(data))
        data = self.data()
        data["rules"][0]["condition"]["value"] = 1
        self.assert_code(data, "condition_value")
        for raw in ([], None, {}, {"schema_version": "policy-ir/1", "documents": [], "rules": []}):
            with self.assertRaises(IRValidationError):
                loads(json.dumps(raw))

    def test_strict_json_and_sanitized_errors(self):
        for raw in ('{"private_marker": NaN}', '{"x":1e999}', '{"x":1,"x":2}', 'not JSON'):
            with self.assertRaises(IRValidationError) as caught:
                loads(raw)
            self.assertNotIn("private_marker", str(caught.exception))
            self.assertNotIn("private_marker", json.dumps(caught.exception.report.to_dict()))

    def test_source_type_and_encoding(self):
        policy, _ = fixture()
        for sources in ([], {"source-1": "text"}, {"source-1": b'\xff'}):
            self.assertFalse(validate_policy(policy, sources).valid)

    def test_direct_invalid_model_rejected(self):
        policy, sources = fixture()
        bad = replace(policy, rules=(replace(policy.rules[0], source_refs=()),))
        with self.assertRaises(IRValidationError):
            dumps(bad)
        self.assertFalse(validate_policy(bad, sources).valid)

    def test_empty_rules_are_only_an_envelope(self):
        policy, sources = fixture()
        report = validate_policy(replace(policy, rules=()), sources)
        self.assertTrue(report.valid)
        self.assertFalse(report.compilable)

    def test_shape_report_is_not_provenance_approval(self):
        report = validate_data(self.data())
        self.assertTrue(report.valid)
        self.assertFalse(report.provenance_checked)
        self.assertFalse(report.compilable)


if __name__ == "__main__":
    unittest.main()
