"""Independently authored synthetic contracts; no toolkit fixtures."""

from contextlib import redirect_stdout
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from builder.brief.openapi import load_contract, load_document, main


def fixture():
    return {"openapi": "3.0.3", "info": {"title": "Synthetic", "version": "1"},
            "paths": {"/widgets/{widget}": {
                "parameters": [{"name": "widget", "in": "path", "required": True,
                                "schema": {"type": "string"}}],
                "post": {"requestBody": {"required": True, "content": {
                    "application/json": {"schema": {"type": "object",
                        "additionalProperties": False, "required": ["size"],
                        "properties": {"size": {"type": "integer", "minimum": 1,
                                                "maximum": 5, "enum": [1, 2, 5]}}}}}},
                    "responses": {"201": {"description": "Created", "content": {
                        "application/json": {"schema": {"type": "object", "properties": {
                            "accepted": {"type": "boolean"}}}}}},
                        "default": {"description": "Other"}}}}}}


class OpenAPITests(unittest.TestCase):
    def codes(self, result):
        return {d.code for d in result.diagnostics}

    def operation(self, doc):
        return doc["paths"]["/widgets/{widget}"]["post"]

    def schema(self, doc):
        return self.operation(doc)["requestBody"]["content"]["application/json"]["schema"]

    def test_normalized_contract(self):
        result = load_document(fixture())
        self.assertTrue(result.valid, result.diagnostics)
        op = result.contract.operations[0]
        self.assertEqual((op.method, op.path, op.identity),
                         ("POST", "/widgets/{widget}", "POST /widgets/{widget}"))
        self.assertEqual(op.identity_source, "method_path")
        self.assertTrue(op.parameters[0].required)
        self.assertTrue(op.request_required)
        schema = op.request_content[0].schema
        self.assertEqual(schema["required"], ["size"])
        self.assertEqual(schema["properties"]["size"]["enum"], [1, 2, 5])
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual([r.status for r in op.responses], ["201", "default"])
        self.assertIsNone(op.responses[-1].content[0].schema if op.responses[-1].content else None)
        json.dumps(result.to_dict(), allow_nan=False)

    def test_explicit_identity_and_collision(self):
        doc = fixture()
        self.operation(doc)["operationId"] = "make_widget"
        doc["paths"]["/widgets/{widget}"]["get"] = deepcopy(self.operation(doc))
        self.assertIn("duplicate_operation_identity", self.codes(load_document(doc)))
        del doc["paths"]["/widgets/{widget}"]["get"]
        self.assertEqual(load_document(doc).contract.operations[0].identity, "make_widget")
        self.operation(doc)["operationId"] = ""
        self.assertFalse(load_document(doc).valid)

    def test_local_references_and_escaping(self):
        doc = fixture()
        schema = deepcopy(self.schema(doc))
        doc["components"] = {"schemas": {"a/b~c": schema}, "parameters": {
            "Widget": doc["paths"]["/widgets/{widget}"]["parameters"][0]},
            "requestBodies": {"Input": self.operation(doc)["requestBody"]},
            "responses": {"Created": self.operation(doc)["responses"]["201"]}}
        doc["components"]["requestBodies"]["Input"]["content"]["application/json"]["schema"] = {"$ref": "#/components/schemas/a~1b~0c"}
        self.operation(doc)["requestBody"] = {"$ref": "#/components/requestBodies/Input"}
        self.operation(doc)["responses"]["201"] = {"$ref": "#/components/responses/Created"}
        doc["paths"]["/widgets/{widget}"]["parameters"] = [{"$ref": "#/components/parameters/Widget"}]
        result = load_document(doc)
        self.assertTrue(result.valid, result.diagnostics)
        self.assertEqual(result.contract.operations[0].request_content[0].schema, schema)

    def test_reference_failures(self):
        for ref in ("remote.json#/schema", "https://invalid.example/schema", "#/missing", "#/bad~2escape", "#/percent%20name"):
            doc = fixture()
            self.operation(doc)["requestBody"] = {"$ref": ref}
            self.assertFalse(load_document(doc).valid)
        doc = fixture()
        doc["components"] = {"schemas": {"Recursive": {"type": "object", "properties": {
            "child": {"$ref": "#/components/schemas/Recursive"}}}}}
        self.operation(doc)["requestBody"]["content"]["application/json"]["schema"] = {"$ref": "#/components/schemas/Recursive"}
        self.assertIn("cyclic_reference", self.codes(load_document(doc)))

    def test_reference_siblings(self):
        doc = fixture()
        self.operation(doc)["requestBody"] = {"$ref": "#/missing", "description": "extra"}
        self.assertIn("reference_siblings", self.codes(load_document(doc)))

    def test_parameter_override(self):
        doc = fixture()
        path = doc["paths"]["/widgets/{widget}"]
        path["parameters"].append({"name": "mode", "in": "query", "schema": {"type": "string"}})
        self.operation(doc)["parameters"] = [{"name": "mode", "in": "query", "required": True,
                                             "style": "form", "explode": False,
                                             "schema": {"type": "string", "enum": ["fast"]}}]
        result = load_document(doc)
        self.assertTrue(result.valid)
        query = next(p for p in result.contract.operations[0].parameters if p.location == "query")
        self.assertTrue(query.required)
        self.assertEqual(query.schema["enum"], ["fast"])
        self.assertEqual(query.serialization, {"style": "form", "explode": False})

    def test_invalid_parameters(self):
        for parameters in ({}, [None], [{"name": "widget", "in": "path", "schema": {}}],
                           [{"name": "x", "in": [], "schema": {}}],
                           [{"name": "x", "in": "query", "content": {}}]):
            doc = fixture()
            self.operation(doc)["parameters"] = parameters
            self.assertFalse(load_document(doc).valid)
        doc = fixture()
        shared = doc["paths"]["/widgets/{widget}"]["parameters"]
        shared.append(deepcopy(shared[0]))
        self.assertIn("duplicate_parameter", self.codes(load_document(doc)))
        doc = fixture()
        doc["paths"]["/widgets/{widget}"]["parameters"] = []
        self.assertIn("path_parameters", self.codes(load_document(doc)))

    def test_multiple_media_and_status_range(self):
        doc = fixture()
        self.operation(doc)["requestBody"]["content"]["text/plain"] = {"schema": {"type": "string"}}
        self.operation(doc)["responses"]["2XX"] = {"description": "Success"}
        result = load_document(doc)
        self.assertTrue(result.valid)
        self.assertEqual(len(result.contract.operations[0].request_content), 2)

    def test_unsupported_constructs(self):
        for key in ("allOf", "oneOf", "anyOf", "not", "discriminator", "unevaluatedProperties"):
            doc = fixture()
            self.schema(doc)[key] = []
            result = load_document(doc)
            self.assertFalse(result.valid)
            self.assertIn("unsupported_schema_keyword", self.codes(result))
        doc = fixture()
        self.operation(doc)["callbacks"] = {"callback": {}}
        doc["webhooks"] = {"event": {}}
        self.assertFalse(load_document(doc).valid)

    def test_31_schema(self):
        doc = fixture()
        doc["openapi"] = "3.1.0"
        self.schema(doc)["properties"]["size"] = {"type": ["integer", "null"], "const": 2, "exclusiveMinimum": 0}
        self.schema(doc)["properties"]["anything"] = True
        self.assertTrue(load_document(doc).valid)
        doc["openapi"] = "3.0.3"
        self.assertFalse(load_document(doc).valid)

    def test_invalid_schema_constraints(self):
        for key, value in (("required", [2]), ("required", ["size", "size"]), ("enum", {}),
                           ("minimum", "one"), ("maxLength", -1), ("uniqueItems", "yes"),
                           ("items", []), ("additionalProperties", None), ("type", "unknown")):
            doc = fixture()
            self.schema(doc)[key] = value
            self.assertFalse(load_document(doc).valid, key)

    def test_version_and_structure(self):
        for version in ("2.0", "3.2.0", None):
            doc = fixture()
            doc["openapi"] = version
            self.assertFalse(load_document(doc).valid)
        for doc in ([], {}, {"openapi": "3.0.3", "paths": []}):
            self.assertFalse(load_document(doc).valid)
        for responses in ({}, [], {"999": {"description": "bad"}}, {"200": None}):
            doc = fixture()
            self.operation(doc)["responses"] = responses
            self.assertFalse(load_document(doc).valid)

    def test_optional_request_and_warnings(self):
        doc = fixture()
        del self.operation(doc)["requestBody"]
        doc["security"] = [{"SyntheticAuth": []}]
        self.operation(doc)["responses"]["201"]["headers"] = {"X-Example": {"schema": {"type": "string"}}}
        result = load_document(doc)
        self.assertTrue(result.valid)
        self.assertEqual(result.contract.operations[0].request_content, ())
        self.assertIn("security_not_normalized", self.codes(result))

    def test_serialization_and_dialect_diagnostics(self):
        doc = fixture()
        self.operation(doc)["parameters"] = [{"name": "x", "in": "header", "schema": {},
                                             "style": "deepObject", "explode": "yes"}]
        result = load_document(doc)
        self.assertIn("parameter_style", self.codes(result))
        self.assertIn("parameter_serialization", self.codes(result))
        for key, value in (("$id", "private_base"), ("$schema", "private_dialect"),
                           ("enum", [1, 1]), ("type", [])):
            doc = fixture()
            self.schema(doc)[key] = value
            self.assertFalse(load_document(doc).valid)

    def test_nested_array_and_boolean_additional_properties(self):
        doc = fixture()
        self.schema(doc)["properties"]["rows"] = {"type": "array", "minItems": 1,
            "items": {"type": "object", "additionalProperties": {"type": "string"}}}
        self.assertTrue(load_document(doc).valid)

    def test_failed_cli_hides_reference_values(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "bad.json"
            doc = fixture()
            self.operation(doc)["requestBody"] = {"$ref": "private_marker"}
            path.write_text(json.dumps(doc), encoding="utf-8")
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(main([str(path)]), 1)
            self.assertNotIn("private_marker", output.getvalue())
            self.assertIn("external_reference", output.getvalue())

    def test_deterministic_and_unchanged(self):
        doc = fixture()
        before = deepcopy(doc)
        first = load_document(doc).to_dict()
        reordered = dict(reversed(list(doc.items())))
        self.assertEqual(first, load_document(reordered).to_dict())
        self.assertEqual(doc, before)
        first["contract"]["operations"][0]["request_content"][0]["schema"]["properties"].clear()
        self.assertEqual(doc, before)

    def test_file_cli_and_sanitization(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "spec.json"
            doc = fixture()
            self.operation(doc)["operationId"] = "private_marker"
            self.schema(doc)["description"] = "private_marker"
            original = json.dumps(doc).encode()
            path.write_bytes(original)
            result = load_contract(path)
            self.assertTrue(result.valid)
            self.assertEqual(result.contract.source_sha256, hashlib.sha256(original).hexdigest())
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(main([str(path)]), 0)
            self.assertNotIn("private_marker", output.getvalue())
            self.assertNotIn("/widgets", output.getvalue())
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(list(Path(folder).iterdir()), [path])
            for raw in (b'{"openapi":"3.0.3","openapi":"3.1.0"}', b'{"x": NaN}', b'{"x":1e999}', b'\xff'):
                path.write_bytes(raw)
                self.assertFalse(load_contract(path).valid)
            self.assertFalse(load_contract(Path(folder) / "missing").valid)


if __name__ == "__main__":
    unittest.main()
