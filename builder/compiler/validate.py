"""Offline structural checks, not an official Freya acceptance validator."""

import argparse
from dataclasses import asdict, dataclass, field
import json
import math
from pathlib import Path


@dataclass
class ValidationReport:
    errors: list[dict[str, str]] = field(default_factory=list)
    scope: str = "offline_structure"

    @property
    def valid(self):
        return not self.errors

    def to_dict(self):
        return {"valid": self.valid, **asdict(self)}

    def add(self, code, path, message):
        self.errors.append({"code": code, "path": path, "message": message})


def parse_workflow(text):
    """Reject duplicate keys, nonfinite constants and overflowing JSON numbers."""
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result

    def reject_constant(_):
        raise ValueError("Nonfinite JSON number")

    def finite_float(value):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("Nonfinite JSON number")
        return number

    return json.loads(text, object_pairs_hook=unique,
                      parse_constant=reject_constant, parse_float=finite_float)


def validate_workflow(workflow):
    """Check common graph/tool shapes without enforcing undocumented semantics.

    Paths use numeric positions rather than input identifiers to avoid echoing
    confidential names or values in diagnostics.
    """
    report = ValidationReport()
    if not isinstance(workflow, dict):
        report.add("root_type", "$", "Workflow root must be an object.")
        return report
    initial = workflow.get("initial_node")
    if not isinstance(initial, str) or not initial.strip():
        report.add("initial_node_type", "$.initial_node", "initial_node must be a nonempty string.")
    nodes = workflow.get("nodes")
    if not isinstance(nodes, dict) or not nodes:
        report.add("nodes_type", "$.nodes", "nodes must be a nonempty object mapping.")
        nodes = {}
    if isinstance(initial, str) and initial.strip() and initial not in nodes:
        report.add("initial_node_reference", "$.initial_node", "Initial node does not exist in nodes.")
    global_nodes = workflow.get("global_nodes", {})
    if not isinstance(global_nodes, dict):
        report.add("global_nodes_type", "$.global_nodes", "global_nodes must be an object mapping when present.")
        global_nodes = {}
    targets = set(nodes) | set(global_nodes)

    def tools(value, path):
        if not isinstance(value, list):
            report.add("tools_type", path, "tools must be an array when present.")
            return
        for index, tool in enumerate(value):
            location = f"{path}[{index}]"
            if not isinstance(tool, dict):
                report.add("tool_type", location, "Each tool must be an object.")
                continue
            if not isinstance(tool.get("type"), str) or not tool["type"].strip():
                report.add("tool_kind", location + ".type", "Tool type must be a nonempty string.")
            function = tool.get("function")
            if not isinstance(function, dict):
                report.add("tool_function", location + ".function", "Tool function must be an object.")
                continue
            if not isinstance(function.get("name"), str) or not function["name"].strip():
                report.add("tool_name", location + ".function.name", "Function name must be a nonempty string.")
            for key in ("parameters",):
                if key in function and not isinstance(function[key], dict):
                    report.add("tool_parameters", location + ".function." + key, "Parameters must be an object when present.")
            if "handler" in function and not isinstance(function["handler"], str):
                report.add("tool_handler", location + ".function.handler", "Handler must be a string when present.")
            if "config" in tool and not isinstance(tool["config"], dict):
                report.add("tool_config", location + ".config", "Tool config must be an object when present.")

    if "tools" in workflow:
        tools(workflow["tools"], "$.tools")
    for mapping, prefix in ((nodes, "$.nodes"), (global_nodes, "$.global_nodes")):
        for index, (name, node) in enumerate(mapping.items()):
            location = f"{prefix}[{index}]"
            if not isinstance(name, str) or not name.strip():
                report.add("node_name", location, "Node identifier must be a nonempty string.")
            if not isinstance(node, dict):
                report.add("node_type", location, "Each node must be an object.")
                continue
            if "tools" in node:
                tools(node["tools"], location + ".tools")
            if "transitions" not in node:
                continue
            transitions = node["transitions"]
            if not isinstance(transitions, list):
                report.add("transitions_type", location + ".transitions", "Transitions must be an array when present.")
                continue
            for number, transition in enumerate(transitions):
                path = f"{location}.transitions[{number}]"
                if not isinstance(transition, dict):
                    report.add("transition_type", path, "Each transition must be an object.")
                    continue
                target = transition.get("target_node")
                if not isinstance(target, str) or not target.strip():
                    report.add("transition_target_type", path + ".target_node", "Target must be a nonempty string.")
                elif target not in targets:
                    report.add("transition_target_reference", path + ".target_node", "Target node does not exist.")
    return report


def validate_file(path):
    report = ValidationReport()
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        report.add("file_read", "$", "Cannot read workflow as UTF-8 text.")
        return report
    try:
        workflow = parse_workflow(text)
    except (ValueError, RecursionError):
        # Exception text can include document content; do not echo it.
        report.add("json_parse", "$", "Invalid JSON: check syntax, duplicate keys, and nonfinite numbers.")
        return report
    return validate_workflow(workflow)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workflow", help="Local workflow JSON path")
    parser.add_argument("--json", action="store_true", help="Print a machine-readable report")
    args = parser.parse_args(argv)
    report = validate_file(args.workflow)
    if args.json:
        print(json.dumps(report.to_dict(), allow_nan=False))
    elif report.valid:
        print("PASS: offline structural checks only; official acceptance is unverified.")
    else:
        for error in report.errors:
            print(f"{error['code']} at {error['path']}: {error['message']}")
    return 0 if report.valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
