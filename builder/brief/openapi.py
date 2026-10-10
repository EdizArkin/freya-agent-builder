"""Offline OpenAPI contract inspection. Never fetch references or print input values."""

import argparse
from copy import deepcopy
from dataclasses import asdict, dataclass, field
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from builder.compiler.validate import parse_workflow


@dataclass(frozen=True)
class Diagnostic:
    code: str
    location: str
    message: str
    severity: str = "error"


@dataclass(frozen=True)
class Parameter:
    name: str
    location: str
    required: bool
    schema: dict[str, Any] | bool | None
    serialization: dict[str, Any]


@dataclass(frozen=True)
class MediaSchema:
    media_type: str
    schema: dict[str, Any] | bool | None


@dataclass(frozen=True)
class Response:
    status: str
    content: tuple[MediaSchema, ...]


@dataclass(frozen=True)
class Operation:
    identity: str
    identity_source: str
    method: str
    path: str
    parameters: tuple[Parameter, ...]
    request_required: bool
    request_content: tuple[MediaSchema, ...]
    responses: tuple[Response, ...]


@dataclass(frozen=True)
class Contract:
    openapi_version: str
    source_sha256: str
    operations: tuple[Operation, ...]


@dataclass
class LoadResult:
    contract: Contract | None = None
    diagnostics: list[Diagnostic] = field(default_factory=list)

    @property
    def valid(self):
        return self.contract is not None and not any(d.severity == "error" for d in self.diagnostics)

    def to_dict(self):
        # Full representation may contain private schema values. CLI does not use it.
        return {"valid": self.valid, "contract": asdict(self.contract) if self.contract else None,
                "diagnostics": [asdict(d) for d in self.diagnostics]}


METHODS = frozenset({"get", "put", "post", "delete", "options", "head", "patch", "trace"})
SCHEMA_KEYS = frozenset({
    "type", "properties", "required", "items", "additionalProperties", "enum", "const",
    "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
    "minLength", "maxLength", "pattern", "format", "minItems", "maxItems", "uniqueItems",
    "minProperties", "maxProperties", "nullable", "description", "title", "default",
    "example", "examples", "deprecated", "readOnly", "writeOnly", "$schema", "$id"})


class _Loader:
    def __init__(self, document, digest):
        self.doc = deepcopy(document)
        self.digest = digest
        self.result = LoadResult()
        self.version = ""

    def issue(self, code, location, message, severity="error"):
        self.result.diagnostics.append(Diagnostic(code, location, message, severity))

    def resolve(self, value, location, active=()):
        """Resolve same-document pointers, including escaped property names."""
        if not isinstance(value, dict) or "$ref" not in value:
            return deepcopy(value)
        ref = value["$ref"]
        if not isinstance(ref, str) or not ref.startswith("#/"):
            self.issue("external_reference", location, "Use a same-document #/ JSON pointer; external references are not fetched.")
            return None
        if ref in active:
            self.issue("cyclic_reference", location, "Recursive references are unsupported; provide a finite schema profile.")
            return None
        if len(value) != 1:
            self.issue("reference_siblings", location, "Reference siblings require version-specific semantics; use a standalone reference.")
            return None
        try:
            target = self.doc
            for token in ref[2:].split("/"):
                if re.search(r"~(?![01])", token) or "%" in token:
                    raise ValueError()
                token = token.replace("~1", "/").replace("~0", "~")
                if isinstance(target, list):
                    if not re.fullmatch(r"0|[1-9][0-9]*", token):
                        raise ValueError()
                    target = target[int(token)]
                else:
                    target = target[token]
        except (KeyError, IndexError, TypeError, ValueError):
            self.issue("unresolved_reference", location, "Reference does not resolve to a supported local JSON pointer.")
            return None
        # Resolve nested references with the same ancestry during schema traversal.
        return self.resolve(target, location, active + (ref,)) if isinstance(target, dict) and "$ref" in target else deepcopy(target)

    def schema(self, value, location, active=()):
        ref = value.get("$ref") if isinstance(value, dict) else None
        if ref in active and ref is not None:
            self.issue("cyclic_reference", location, "Recursive schemas are unsupported; provide a finite schema profile.")
            return None
        value = self.resolve(value, location, active)
        if ref is not None:
            active = active + (ref,)
        if isinstance(value, bool) and self.version.startswith("3.1."):
            return value
        if not isinstance(value, dict):
            self.issue("schema_type", location, "Schema must be an object (or a boolean in OpenAPI 3.1).")
            return None
        out = {}
        for key in sorted(value):
            item = value[key]
            if key.startswith("x-"):
                self.issue("schema_extension", location, "Vendor schema extension is omitted; check whether it affects execution.", "warning")
            elif key not in SCHEMA_KEYS:
                self.issue("unsupported_schema_keyword", location, "Schema uses an unsupported keyword; simplify or extend the loader before use.")
            elif key == "properties":
                if not isinstance(item, dict):
                    self.issue("properties_type", location, "Schema properties must be an object.")
                else:
                    out[key] = {name: self.schema(item[name], f"{location}.property[{i}]", active)
                                for i, name in enumerate(sorted(item))}
            elif key == "additionalProperties" and isinstance(item, bool):
                out[key] = item
            elif key in {"items", "additionalProperties"} and isinstance(item, (dict, bool)):
                out[key] = self.schema(item, location + "." + key, active)
            else:
                out[key] = deepcopy(item)
        if "required" in out:
            required = out["required"]
            if not isinstance(required, list) or any(not isinstance(x, str) for x in required) or len(set(required)) != len(required):
                self.issue("required_fields", location, "required must be an array of unique field names.")
            else:
                out["required"] = sorted(required)
        if "enum" in out and (not isinstance(out["enum"], list) or not out["enum"]):
            self.issue("enum_type", location, "enum must be a nonempty array.")
        elif "enum" in out:
            encoded_values = [json.dumps(v, sort_keys=True, allow_nan=False) for v in out["enum"]]
            if len(set(encoded_values)) != len(encoded_values):
                self.issue("enum_duplicate", location, "enum must not contain duplicate JSON values.")
        kind = out.get("type")
        kinds = kind if isinstance(kind, list) else [kind] if kind is not None else []
        allowed = {"object", "array", "string", "integer", "number", "boolean"}
        if self.version.startswith("3.1."):
            allowed.add("null")
        if (isinstance(kind, list) and not self.version.startswith("3.1.")) or any(not isinstance(k, str) or k not in allowed for k in kinds):
            self.issue("schema_value_type", location, "Schema type is invalid for this OpenAPI version.")
        elif isinstance(kind, list) and (not kind or len(set(kind)) != len(kind)):
            self.issue("schema_value_type", location, "Schema type array must be nonempty and unique.")
        if "items" in value and not isinstance(value["items"], (dict, bool)):
            self.issue("items_type", location, "items must be a schema.")
        if "additionalProperties" in value and not isinstance(value["additionalProperties"], (dict, bool)):
            self.issue("additional_properties_type", location, "additionalProperties must be a schema or boolean.")
        for key in ("minimum", "maximum", "multipleOf"):
            if key in out and type(out[key]) not in (int, float):
                self.issue("constraint_type", location, "Numeric constraint must be a number.")
        for key in ("minLength", "maxLength", "minItems", "maxItems", "minProperties", "maxProperties"):
            if key in out and (type(out[key]) is not int or out[key] < 0):
                self.issue("constraint_type", location, "Length/count constraint must be a nonnegative integer.")
        for key in ("nullable", "uniqueItems", "readOnly", "writeOnly", "deprecated"):
            if key in out and type(out[key]) is not bool:
                self.issue("constraint_type", location, "Boolean schema flag must be boolean.")
        for key in ("exclusiveMinimum", "exclusiveMaximum"):
            expected = (int, float) if self.version.startswith("3.1.") else (bool,)
            if key in out and type(out[key]) not in expected:
                self.issue("constraint_type", location, "Exclusive bound has the wrong type for this OpenAPI version.")
        for key in ("pattern", "format"):
            if key in out and not isinstance(out[key], str):
                self.issue("constraint_type", location, "Pattern/format must be a string.")
        if "$id" in out:
            self.issue("schema_base_uri", location, "Schema base URI changes are unsupported; remove $id or extend reference handling.")
        if "$schema" in out and out["$schema"] != "https://json-schema.org/draft/2020-12/schema":
            self.issue("schema_dialect", location, "Custom schema dialect is unsupported; use the supported OpenAPI schema profile.")
        if self.version.startswith("3.0.") and "const" in out:
            self.issue("version_keyword", location, "const is not supported in OpenAPI 3.0 schemas; use enum.")
        if self.version.startswith("3.1.") and "nullable" in out:
            self.issue("version_keyword", location, "OpenAPI 3.1 uses null in type rather than nullable.")
        return out

    def content(self, value, location):
        if not isinstance(value, dict):
            self.issue("content_type", location, "content must be a media-type mapping.")
            return ()
        output = []
        for index, media in enumerate(sorted(value)):
            item = value[media]
            loc = f"{location}.media[{index}]"
            if not isinstance(item, dict):
                self.issue("media_type", loc, "Media definition must be an object.")
                continue
            if "encoding" in item:
                self.issue("unsupported_encoding", loc, "Custom media encoding is unsupported; add explicit serialization support.")
            output.append(MediaSchema(media, self.schema(item["schema"], loc) if "schema" in item else None))
        return tuple(output)

    def parameters(self, value, location):
        if not isinstance(value, list):
            self.issue("parameters_type", location, "parameters must be an array.")
            return {}
        output = {}
        for index, raw in enumerate(value):
            loc = f"{location}[{index}]"
            item = self.resolve(raw, loc)
            if not isinstance(item, dict):
                self.issue("parameter_type", loc, "Parameter must resolve to an object.")
                continue
            name, place = item.get("name"), item.get("in")
            if not isinstance(name, str) or not name or not isinstance(place, str) or place not in {"path", "query", "header", "cookie"}:
                self.issue("parameter_identity", loc, "Parameter needs a name and supported in location.")
                continue
            required = item.get("required", False)
            if type(required) is not bool or (place == "path" and required is not True):
                self.issue("parameter_required", loc, "required must be boolean; path parameters must explicitly be required.")
                required = required is True
            if "content" in item or "schema" not in item:
                self.issue("parameter_content", loc, "Only schema-based parameters are supported.")
                continue
            styles = {"path": {"matrix", "label", "simple"}, "query": {"form", "spaceDelimited", "pipeDelimited", "deepObject"},
                      "header": {"simple"}, "cookie": {"form"}}
            if "style" in item and (not isinstance(item["style"], str) or item["style"] not in styles[place]):
                self.issue("parameter_style", loc, "Parameter style is unsupported for its location.")
            for flag in ("explode", "allowReserved", "allowEmptyValue"):
                if flag in item and type(item[flag]) is not bool:
                    self.issue("parameter_serialization", loc, "Parameter serialization flags must be boolean.")
            key = (place, name)
            if key in output:
                self.issue("duplicate_parameter", loc, "Duplicate parameter in the same scope; remove ambiguity.")
            output[key] = Parameter(name, place, required, self.schema(item["schema"], loc),
                                    {k: deepcopy(item[k]) for k in ("style", "explode", "allowReserved", "allowEmptyValue") if k in item})
        return output

    def run(self):
        if not isinstance(self.doc, dict):
            self.issue("document_type", "$", "OpenAPI root must be an object.")
            return self.result
        self.version = self.doc.get("openapi", "")
        if not isinstance(self.version, str) or not re.fullmatch(r"3\.[01]\.\d+", self.version):
            self.issue("openapi_version", "$", "Supported versions are OpenAPI 3.0.x and 3.1.x.")
            return self.result
        paths = self.doc.get("paths")
        if not isinstance(paths, dict):
            self.issue("paths_type", "$", "paths must be an object.")
            return self.result
        if self.doc.get("webhooks"):
            self.issue("unsupported_webhooks", "$", "Webhook contracts are outside this loader's operation profile.")
        operations, identities = [], set()
        for index, path in enumerate(sorted(paths)):
            loc = f"paths[{index}]"
            item = self.resolve(paths[path], loc)
            if not path.startswith("/") or not isinstance(item, dict):
                self.issue("path_item", loc, "Path must start with / and resolve to an object.")
                continue
            shared = self.parameters(item.get("parameters", []), loc + ".parameters")
            for method in sorted(METHODS & item.keys()):
                op = item[method]
                oloc = loc + "." + method
                if not isinstance(op, dict):
                    self.issue("operation_type", oloc, "Operation must be an object.")
                    continue
                identity = op.get("operationId", method.upper() + " " + path)
                if not isinstance(identity, str) or not identity.strip():
                    self.issue("operation_identity", oloc, "operationId must be a nonempty string when present.")
                    identity = method.upper() + " " + path
                if identity in identities:
                    self.issue("duplicate_operation_identity", oloc, "Operation identity collides; assign unique operationId values.")
                identities.add(identity)
                params = {**shared, **self.parameters(op.get("parameters", []), oloc + ".parameters")}
                placeholders = set(re.findall(r"\{([^{}]+)\}", path))
                declared = {n for place, n in params if place == "path"}
                if placeholders != declared:
                    self.issue("path_parameters", oloc, "Path placeholders and declared path parameters must match.")
                body = self.resolve(op.get("requestBody", {}), oloc + ".requestBody")
                if not isinstance(body, dict):
                    self.issue("request_body_type", oloc, "requestBody must resolve to an object.")
                    body = {}
                required = body.get("required", False)
                if type(required) is not bool:
                    self.issue("request_required", oloc, "Request body required must be boolean.")
                    required = False
                if "requestBody" in op and not body.get("content"):
                    self.issue("request_content", oloc, "Request body needs nonempty content.")
                responses = op.get("responses")
                normalized = []
                if not isinstance(responses, dict) or not responses:
                    self.issue("responses_type", oloc, "Operation needs a nonempty responses mapping.")
                else:
                    for number, status in enumerate(sorted(responses)):
                        rloc = f"{oloc}.responses[{number}]"
                        if not re.fullmatch(r"default|[1-5](?:[0-9]{2}|XX)", status):
                            self.issue("response_status", rloc, "Response key must be a status code, status range, or default.")
                        response = self.resolve(responses[status], rloc)
                        if not isinstance(response, dict):
                            self.issue("response_type", rloc, "Response must resolve to an object.")
                            continue
                        if response.get("links") or response.get("headers"):
                            self.issue("response_metadata", rloc, "Response headers/links are not normalized; review before integration.", "warning")
                        normalized.append(Response(status, self.content(response.get("content", {}), rloc)))
                if op.get("callbacks"):
                    self.issue("unsupported_callbacks", oloc, "Callback operations require separate support.")
                if op.get("security", self.doc.get("security")):
                    self.issue("security_not_normalized", oloc, "Security requirements are outside this contract; configure authentication separately.", "warning")
                operations.append(Operation(identity, "operationId" if "operationId" in op else "method_path",
                    method.upper(), path, tuple(params[k] for k in sorted(params)), required,
                    self.content(body.get("content", {}), oloc + ".requestBody"), tuple(normalized)))
        self.result.contract = Contract(self.version, self.digest, tuple(operations))
        return self.result


def load_document(document):
    """Load an already parsed document without mutating it."""
    try:
        encoded = json.dumps(document, sort_keys=True, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, RecursionError):
        return LoadResult(diagnostics=[Diagnostic("document_json", "$", "Input must be finite JSON data.")])
    try:
        return _Loader(parse_workflow(encoded.decode("utf-8")), hashlib.sha256(encoded).hexdigest()).run()
    except RecursionError:
        return LoadResult(diagnostics=[Diagnostic("depth_limit", "$", "Document/reference nesting exceeds the supported depth; simplify the specification.")])


def load_contract(path):
    """Read UTF-8 JSON; source digest identifies the unchanged original bytes."""
    try:
        raw = Path(path).read_bytes()
        document = parse_workflow(raw.decode("utf-8"))
    except (OSError, UnicodeError, ValueError, RecursionError):
        return LoadResult(diagnostics=[Diagnostic("source_read_parse", "$",
            "Cannot load strict UTF-8 JSON; check file access, syntax, duplicate keys and finite numbers.")])
    try:
        return _Loader(document, hashlib.sha256(raw).hexdigest()).run()
    except RecursionError:
        return LoadResult(diagnostics=[Diagnostic("depth_limit", "$", "Document/reference nesting exceeds the supported depth; simplify the specification.")])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("specification", help="Local OpenAPI JSON path")
    args = parser.parse_args(argv)
    result = load_contract(args.specification)
    # Never print names, paths, enum values, schema defaults, descriptions or endpoints.
    operations = result.contract.operations if result.contract else ()
    print(f"{'PASS' if result.valid else 'FAIL'}: {len(operations)} operations; offline contract profile only")
    for index, operation in enumerate(operations):
        print(f"operation[{index}]: {operation.method}; identity={operation.identity_source}; "
              f"parameters={len(operation.parameters)}; request media={len(operation.request_content)}; "
              f"responses={len(operation.responses)}")
    for diagnostic in result.diagnostics:
        print(f"{diagnostic.severity}: {diagnostic.code} at {diagnostic.location}: {diagnostic.message}")
    return 0 if result.valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
