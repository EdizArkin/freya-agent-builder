"""Fail-closed compiler boundary for current non-executable candidate policies."""
import argparse
import json
from pathlib import Path

from builder.brief.sources import load_briefs, SourceError
from builder.policy.candidate import loads, check_coverage
from builder.policy.codec import IRValidationError
from builder.policy.composition import compose


def compile_candidate(text, sources):
    """Validate evidence and precedence; never turn action labels into side effects."""
    candidate = loads(text, sources)
    coverage = check_coverage(candidate, sources)
    composition = compose(candidate)
    findings = [f.code for f in coverage.findings + composition.report.findings]
    if not candidate.rules:
        findings.append('mandatory_rules_missing')
    # No current profile has semantic approval or runtime capability validation.
    findings.extend(('ir_not_executable', 'tool_bindings_missing',
                     'semantic_validation_missing', 'runtime_capability_checks_missing'))
    return {'compiled': False, 'compilable': False, 'workflow': None,
            'findings': sorted(set(findings)), 'effective_rule_count': len(composition.rules)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--brief', action='append', required=True, help='Ordered original source paths')
    parser.add_argument('--policy', required=True, help='Local policy-candidate/1 JSON')
    parser.add_argument('--output', required=True, help='Reserved workflow destination; never written on failure')
    args = parser.parse_args(argv)
    try:
        sources = load_briefs(args.brief)
        result = compile_candidate(Path(args.policy).read_text(encoding='utf-8'), sources)
        result.pop('workflow')
        print(json.dumps(result, sort_keys=True))
    except IRValidationError as exc:
        print(json.dumps({'compiled': False, 'findings': sorted({f.code for f in exc.report.findings})}))
    except (OSError, UnicodeError, SourceError, ValueError):
        print(json.dumps({'compiled': False, 'findings': ['compiler_input_invalid']}))
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
