"""Synthetic fixtures only; never load local Freya materials in unit tests."""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest

from builder.compiler.validate import main, parse_workflow, validate_file, validate_workflow


def fixture():
    return {"initial_node": "start", "nodes": {
        "start": {"transitions": [{"target_node": "finish"}], "tools": [
            {"type": "function", "function": {"name": "synthetic_action", "parameters": {}}}]},
        "finish": {}}}


class WorkflowTests(unittest.TestCase):
    def assert_code(self, value, code):
        report = validate_workflow(value)
        self.assertFalse(report.valid)
        self.assertIn(code, [error["code"] for error in report.errors])

    def test_valid(self):
        self.assertTrue(validate_workflow(fixture()).valid)

    def test_initial_type(self):
        for initial in (None, 2, "", [], False):
            with self.subTest(initial=initial):
                w = fixture()
                w["initial_node"] = initial
                self.assert_code(w, "initial_node_type")
        w = fixture()
        del w["initial_node"]
        self.assert_code(w, "initial_node_type")

    def test_initial_reference(self):
        w = fixture()
        w["initial_node"] = "missing"
        self.assert_code(w, "initial_node_reference")

    def test_transition_reference(self):
        w = fixture()
        w["nodes"]["start"]["transitions"][0]["target_node"] = "missing"
        self.assert_code(w, "transition_target_reference")

    def test_nodes(self):
        for nodes in (None, [], {}, {"start": None}):
            with self.subTest(nodes=nodes):
                w = fixture()
                w["nodes"] = nodes
                self.assertFalse(validate_workflow(w).valid)

    def test_root(self):
        for value in ([], None, "text", 1):
            self.assert_code(value, "root_type")

    def test_tools(self):
        for value in ({}, [None], [{}], [{"type": "function", "function": []}],
                      [{"type": "function", "function": {"name": "x", "parameters": []}}],
                      [{"type": "function", "function": {"name": "x", "handler": 3}}],
                      [{"type": "function", "function": {"name": "x"}, "config": []}]):
            for scope in ("root", "node"):
                with self.subTest(value=value, scope=scope):
                    w = fixture()
                    container = w if scope == "root" else w["nodes"]["start"]
                    container["tools"] = value
                    self.assertFalse(validate_workflow(w).valid)

    def test_duplicate_keys(self):
        for text in ('{"nodes": {}, "nodes": {}}', '{"a": {"b": 1, "b": 2}}'):
            with self.assertRaises(ValueError):
                parse_workflow(text)

    def test_nonfinite(self):
        for value in ("NaN", "Infinity", "-Infinity", "1e999"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_workflow('{"number": ' + value + '}')

    def test_optional_fields(self):
        w = {"initial_node": "start", "nodes": {"start": {}}}
        self.assertTrue(validate_workflow(w).valid)
        w.update(agent_config={"synthetic_extension": True}, unknown_extension=None)
        self.assertTrue(validate_workflow(w).valid)

    def test_global_target(self):
        w = fixture()
        w["global_nodes"] = {"outside": {}}
        w["nodes"]["start"]["transitions"][0]["target_node"] = "outside"
        self.assertTrue(validate_workflow(w).valid)
        w["global_nodes"] = []
        self.assert_code(w, "global_nodes_type")

    def test_transition_shapes(self):
        for transitions in ({}, [None], [{}], [{"target_node": []}]):
            w = fixture()
            w["nodes"]["start"]["transitions"] = transitions
            self.assertFalse(validate_workflow(w).valid)

    def test_reports_and_cli(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "synthetic.json"
            path.write_text(json.dumps(fixture()), encoding="utf-8")
            self.assertEqual(validate_file(path).to_dict(), {
                "valid": True, "errors": [], "scope": "offline_structure"})
            stream = io.StringIO()
            with redirect_stdout(stream):
                self.assertEqual(main([str(path), "--json"]), 0)
            self.assertTrue(json.loads(stream.getvalue())["valid"])
            path.write_text('{"private_marker": NaN}', encoding="utf-8")
            stream = io.StringIO()
            with redirect_stdout(stream):
                self.assertEqual(main([str(path), "--json"]), 1)
            self.assertNotIn("private_marker", stream.getvalue())
            self.assertEqual(validate_file(path).errors[0]["code"], "json_parse")
            path.write_bytes(b'\xff')
            self.assertEqual(validate_file(path).errors[0]["code"], "file_read")
            self.assertEqual(validate_file(Path(directory) / "missing").errors[0]["code"], "file_read")

    def test_does_not_mutate_or_echo_identifiers(self):
        w = fixture()
        w["nodes"]["confidential_marker"] = {"tools": [None]}
        original = deepcopy(w)
        report = validate_workflow(w)
        self.assertEqual(w, original)
        self.assertNotIn("confidential_marker", json.dumps(report.to_dict()))


if __name__ == "__main__":
    unittest.main()
