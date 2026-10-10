"""Explicit override graph and small, conservative contradiction checks."""
from dataclasses import dataclass

from builder.policy.candidate import CandidateRule
from builder.policy.models import RuleCategory, RuleStatus, ValidationFinding, ValidationReport


@dataclass(frozen=True)
class EffectiveRule:
    rule: CandidateRule
    excluded_selectors: tuple[str, ...] = ()


@dataclass(frozen=True)
class Composition:
    rules: tuple[EffectiveRule, ...]
    # Candidate policy retains both ends and their source references.
    lineage: tuple[tuple[str, str, str | None], ...]
    report: ValidationReport


def compose(policy) -> Composition:
    findings, lineage = [], []
    rules = {r.envelope.id: r for r in policy.rules}
    graph = {ident: set() for ident in rules}
    excluded, removed, replacements = {}, set(), {}
    for index, rule in enumerate(policy.rules):
        loc = f'rules[{index}]'
        if rule.envelope.category == RuleCategory.GUARD and rule.guard is None:
            findings.append(ValidationFinding('guard_missing', loc, 'Conditional guard rule needs an explicit supported guard.'))
        for override in rule.overrides:
            target = rules.get(override.target)
            if target is None:
                findings.append(ValidationFinding('override_missing', loc, 'Override target does not exist.'))
                continue
            if rule.envelope.status != RuleStatus.CANDIDATE or target.envelope.status != RuleStatus.CANDIDATE:
                findings.append(ValidationFinding('override_status', loc, 'Overrides require candidate rules at both ends.'))
                continue
            if target.action != rule.action or target.envelope.scope != rule.envelope.scope:
                findings.append(ValidationFinding('override_domain', loc, 'Override must retain action and scope domain in this profile.'))
                continue
            selection = override.selector
            if (rule.guard != target.guard or (rule.guard is not None and target.guard is not None
                    and type(rule.guard.value) is not type(target.guard.value))):
                findings.append(ValidationFinding('override_guard', loc, 'This profile requires identical guard contexts for replacement.'))
                continue
            if selection is None:
                if rule.selector != target.selector:
                    findings.append(ValidationFinding('override_scope', loc, 'Full override must retain the target selector; use an explicit scoped override.'))
                    continue
            elif selection == '*' or selection != rule.selector or target.selector not in ('*', selection):
                findings.append(ValidationFinding('override_scope', loc, 'Scoped override needs an exact replacement selector within the target domain.'))
                continue
            prior = replacements.setdefault(override.target, [])
            if any(s is None or selection is None or s == selection for s in prior):
                findings.append(ValidationFinding('override_ambiguous', loc, 'Overlapping replacements of one target require explicit unambiguous composition.'))
            prior.append(selection)
            graph[rule.envelope.id].add(override.target)
            lineage.append((override.target, rule.envelope.id, selection))
            if selection is None or target.selector == selection:
                removed.add(override.target)
            else:
                excluded.setdefault(override.target, set()).add(selection)
    done, active = set(), set()

    def visit(ident):
        if ident in active:
            return True
        if ident in done:
            return False
        active.add(ident)
        cyclic = any(visit(t) for t in sorted(graph[ident]))
        active.remove(ident)
        done.add(ident)
        return cyclic

    if any(visit(ident) for ident in sorted(rules)):
        findings.append(ValidationFinding('override_cycle', '$', 'Override graph contains a cycle.'))
    if any(target in excluded and target in removed for target in rules):
        findings.append(ValidationFinding('override_chain_unsupported', '$', 'A partially replaced wildcard cannot also be fully replaced in this profile.'))
    if findings:
        # Do not expose an effective view from an invalid override graph.
        return Composition((), tuple(lineage), ValidationReport(tuple(findings), True))
    effective = tuple(EffectiveRule(r, tuple(sorted(excluded.get(r.envelope.id, ()))))
        for r in policy.rules if r.envelope.id not in removed and r.envelope.status == RuleStatus.CANDIDATE
        and r.envelope.category in (RuleCategory.OBLIGATION, RuleCategory.PROHIBITION, RuleCategory.GUARD))
    for i, left in enumerate(effective):
        for right in effective[i + 1:]:
            a, b = left.rule, right.rule
            if a.action != b.action or a.envelope.scope != b.envelope.scope:
                continue
            if a.selector != '*' and b.selector != '*' and a.selector != b.selector:
                continue
            if a.selector in right.excluded_selectors or b.selector in left.excluded_selectors:
                continue
            # Only identical guard contexts or an unconditional context are compared.
            if a.guard is not None and b.guard is not None and (
                a.guard.field != b.guard.field or a.guard.op != b.guard.op
                or (type(a.guard.value), a.guard.value) != (type(b.guard.value), b.guard.value)):
                continue
            categories = {a.envelope.category, b.envelope.category}
            if categories == {RuleCategory.OBLIGATION, RuleCategory.PROHIBITION}:
                findings.append(ValidationFinding('contradictory_rules', f'effective[{i}]', 'An action is both required and prohibited in an overlapping represented context.'))
            av = {p.name: p.value for p in a.parameters}
            bv = {p.name: p.value for p in b.parameters}
            if a.envelope.category == b.envelope.category == RuleCategory.OBLIGATION and any(
                (type(av[n]), av[n]) != (type(bv[n]), bv[n]) for n in av.keys() & bv.keys()):
                findings.append(ValidationFinding('contradictory_values', f'effective[{i}]', 'Overlapping obligations require incompatible parameter values.'))
    return Composition(effective, tuple(lineage), ValidationReport(tuple(findings), True))
