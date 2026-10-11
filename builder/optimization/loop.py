"""The builder's optimization run: evaluate, diagnose, propose a candidate, deploy it as a new version of the same
workflow, evaluate it, keep it or go back, and do that again within the limits.

    run(...)    steps until nothing more can or may be done; an accepted candidate is the incumbent of the next step
    step(...)   one candidate, from the diagnosis to the decision

The builder owns the run. What it does not own is the evaluation: `evaluation` is handed in (the benchmark harness
runs the simulations, reads the bank and the judge, and keeps the records). Everything else is the builder's own
machinery: the diagnosis (diagnosis.py), the candidate generator (candidate.py), the compiler and validation
(pipeline.release), the platform versions and the rollback (freya/deployment), the decision (policy.py).

The evaluation is any object with:
    cases()                           the ids of the cases that make a result complete
    safety_cases()                    the ids of its safety cases; those the incumbent passes are used when the caller names none
    stored(version_id)                {case id: row}: the results it holds for that version of this slot
    run(version_id, label, only=None) simulate that version now (it must be the published one) and return its rows;
                                      `only` names the cases that still lack a result
A row carries `pass`, `ledger_pass`, `judge_outcome`, `unexpected_records` (the four the decision needs), the
`version_id` and `workflow_id` it was stamped with and the `agent_version` the platform reported for its call (the
three the binding needs), and for the diagnosis `failed`, `gate`, `messages`, `tool_trace`, `ledger`, `expected`.

Order of a step, and where it stops:
1. The incumbent is the platform version that is PUBLISHED now, read from the platform. The slot's history says
   which build that is; its spec comes from the archive of that build, never from the files of the latest build.
   If those files are another build's (a candidate that was rolled back), they are put back first.
2. This builder must compile the incumbent's spec to the incumbent's version id. If it does not, the builder
   itself has changed, and a candidate would differ from the incumbent by more than its change: the step stops,
   or, with `rebuild`, makes this builder's build of the same spec the candidate. A changed builder is tested
   like any other candidate; it is never slipped in with one.
3. Results count only for the version they were stamped with (`bound`). An incumbent without a complete result
   is simulated first, once, inside the limits.
4. The failed cases are filed (diagnosis.plan). One that words can address is chosen, bank records first; the
   others are reported with what they need. A model proposes one change for the chosen case. A proposal that is
   not exactly one narrow change of words, that takes away a guarded sentence, or whose spec does not validate,
   compile or keep what the runtime enforces, ends the step before anything is deployed.
5. The candidate is saved as a new version of the same workflow. From here on EVERY way out goes through the
   decision and `settle`, an interrupt (Ctrl-C, SystemExit) included: a simulation that fails, an exception, a
   missing result or a result of another version all mean "go back".
6. The candidate is evaluated and compared case by case (policy.decide). It stays, or the incumbent's platform
   version is published again and the files of the slot are put back; the published version is read back; a
   rollback that did not happen is reported as such and never as a result.
7. One line is appended to the audit file, whatever happened. An interrupt is raised again after it.
"""
import json
import time
from pathlib import Path

from builder import pipeline
from builder.artifacts import store
from builder.extraction import spend
from builder.freya import deployment, versioning
from builder.optimization import candidate as candidates
from builder.optimization import diagnosis, policy
from builder.spec.openapi import Api
from builder.validation.spec import digest, validate

CREDITS_PER_CASE = 0.25         # more than any simulated call has cost so far (about 0.1): the estimate must not be low
CONTINUES = ('accepted', 'rolled back', 'rejected before deployment')       # after these a further step can do something else


class Stop(Exception):
    """A step that ends before anything is deployed, with the status it ends on."""

    def __init__(self, status, **more):
        Exception.__init__(self, status)
        self.status, self.more = status, more


def label(version_id):
    """The last part of the names of a version's simulations: the first eight digits of its id."""
    return str(version_id)[3:11] or 'unversioned'


def published_build(files, published):
    """The line of the slot's history that put this platform version there: which of our builds it is."""
    for row in reversed(files.history()):
        if row.get('operation') in ('created', 'updated') and row.get('platform_version_id') == published.get('id') and row.get('version_id'):
            return row
    return None


def bound(rows, version_id, workflow_id):
    """(the rows that are results of this version on this workflow, {case id: why not} for the others).
    A result is evidence for a version only if it was stamped with it when its simulation was recorded."""
    kept, dropped = {}, {}
    for cid, row in (rows or {}).items():
        if not isinstance(row, dict) or row.get('version_id') != version_id:
            dropped[cid] = 'a result of version %s' % (row.get('version_id') if isinstance(row, dict) else None)
        elif row.get('workflow_id') != workflow_id:
            dropped[cid] = 'a result on workflow %s' % row.get('workflow_id')
        else:
            kept[cid] = row
    return kept, dropped


def ran_on(rows):
    """The platform versions the calls of these rows say they ran on."""
    return sorted({r.get('agent_version') for r in rows.values()}, key=lambda v: (v is None, str(v)))


def earlier(audit, version_id):
    """What the audit file says was already tried on this incumbent:
    ({case id: candidates that reached a verdict or were refused}, {cases the model declined}, {case id: [changes]})."""
    tried, declined, changes = {}, set(), {}
    lines = Path(audit).read_text(encoding='utf-8').splitlines() if Path(audit).is_file() else []
    for line in lines:
        try:
            past = json.loads(line)
        except ValueError:
            continue
        cid = past.get('diagnosed')
        if not cid or (past.get('incumbent') or {}).get('version_id') != version_id:
            continue
        if past.get('declined'):
            declined.add(cid)
        elif (past.get('decision') or {}).get('gate') in ('safety', 'regression', 'improvement') or str(past.get('status')).startswith('rejected before deployment'):
            tried[cid] = tried.get(cid, 0) + 1
            change = past.get('modification') or {}
            if change.get('field'):
                changes.setdefault(cid, []).append({'field': change['field'], 'find': change.get('find'), 'replace': change.get('replace'), 'outcome': str(past.get('status'))[:200]})
    return tried, declined, changes


def local_deployment(slot, entry, version_id, sha256, state, operation):
    """The deployment record of the slot when a version is published that was not deployed by the last build."""
    return {'slot': slot, 'workflow_id': entry['workflow_id'], 'agent_id': entry.get('agent_id'), 'operation': operation, 'version_id': version_id,
            'platform_version': (state.get('published') or {}).get('number'), 'local_workflow_sha256': sha256,
            'deployed_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'platform': state}


def settle(freya, slot, workflow_id, decision, incumbent_number, candidate_number, trace, reason=''):
    """Carry the decision out on the platform and read back what is published. Returns (history line or None, state)."""
    row = None
    if decision != policy.ACCEPT:
        state = deployment.platform_versions(freya, workflow_id)
        if (state.get('published') or {}).get('number') != incumbent_number:
            row = deployment.rollback(freya, slot, workflow_id, incumbent_number, trace, reason)
    state = deployment.platform_versions(freya, workflow_id)
    want = candidate_number if decision == policy.ACCEPT else incumbent_number
    if (state.get('published') or {}).get('number') != want:
        raise RuntimeError('%s: platform version %s should be published, the platform says %s' % (slot, want, (state.get('published') or {}).get('number')))
    return row, state


def step(slot, document, evaluation, freya, files, limits, audit, client=None, safety_cases=None, best_effort=False, rebuild=False, credits=None,
         check_platform_version=True, registry=versioning.REGISTRY, trace=print):
    """One step. Returns the audit record; record['status'] says how it ended.

    document                the tool API of the slot
    evaluation              the results and the simulations (see the module text)
    client                  the model that proposes the change: the extraction client of the builder, behind its limit
    safety_cases            cases that must pass for a candidate; default: the safety cases of the evaluation that the
                            incumbent passes
    best_effort             the slot was built with --best-effort: its spec names policy it cannot express
    rebuild                 permit a candidate that is this builder's build of the incumbent's own spec
    credits()               the platform's balance, read before and after everything that is paid for
    check_platform_version  hold the candidate's calls to the platform version that was published for them, when the
                            incumbent's own results show that the platform reports that number
    """
    started = time.time()
    record = {'at': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'slot': slot, 'builder_commit': versioning.builder_commit(), 'status': '', 'credits': 0.0}
    usage = []

    def done(status, **more):
        record.update(more, status=status, seconds=round(time.time() - started), limits=limits.used())
        if usage:
            record['model_calls'] = usage
        Path(audit).parent.mkdir(parents=True, exist_ok=True)
        with Path(audit).open('a', encoding='utf-8') as out:
            out.write(json.dumps(record, ensure_ascii=False, default=str) + '\n')
        trace('optimize %s: %s' % (slot, status))
        return record

    def balance():
        """The platform's balance, or None when it cannot be read: a reading that fails never ends a step."""
        if not credits:
            return None
        try:
            value = credits()
        except Exception as exc:
            trace('optimize %s: the credit balance could not be read: %s: %s' % (slot, type(exc).__name__, str(exc)[:200]))
            return None
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None

    def charge(before, most):
        """Count what a paid action cost: the difference of two readings, or the most it can have cost when one is missing."""
        try:
            after = balance()
            measured = before is not None and after is not None
            used = round(max(0.0, before - after), 4) if measured else most
            record['credits'] = round(record['credits'] + used, 4)
            if not measured:
                record['credits_estimated'] = True
            limits.spend(credits=used)
        except Exception as exc:
            record['credits_not_counted'] = '%s: %s' % (type(exc).__name__, str(exc)[:200])

    api = Api(document)
    check, enforced = (lambda s: validate(s, api, best_effort)), (lambda s: digest(s, api))
    try:
        entry = versioning.known(slot, registry)
        if not entry:
            raise Stop('stopped: the slot is not deployed')
        workflow_id = entry['workflow_id']
        cases = list(evaluation.cases())
        named, marked = list(safety_cases or []), list(evaluation.safety_cases())
        if not named and not marked:
            raise Stop('stopped: no safety case was named; a candidate is never promoted without one')
        if [c for c in named if c not in cases]:
            raise Stop('stopped: the safety case(s) %s are not among the cases of the evaluation' % ', '.join(c for c in named if c not in cases))
        needed = sorted(set(cases))
        cost = CREDITS_PER_CASE * max(1, len(needed))

        # 1. The incumbent: what is published, which build that is, and the files of the slot in line with it.
        was = deployment.platform_versions(freya, workflow_id)
        published = (was or {}).get('published')
        if not published:
            raise Stop('stopped: the platform lists no published version of the workflow of %s' % slot)
        built = published_build(files, published)
        if not built:
            raise Stop('stopped: the history of %s does not say which build is platform version %s (%s); deploy the slot with this builder first'
                       % (slot, published.get('number'), published.get('id')))
        incumbent_id = built['version_id']
        spec = files.read(store.SPEC, incumbent_id)
        if spec is None:
            raise Stop('stopped: version %s of %s has no archived spec' % (incumbent_id, slot))
        record.update(workflow_id=workflow_id, incumbent={'version_id': incumbent_id, 'platform_version': published, 'builder_commit': built.get('builder_commit')})
        latest = files.read(store.DEPLOYMENT, default={})
        if latest.get('version_id') == incumbent_id:
            files.keep(incumbent_id, store.EXPORT)          # the export that is there is the incumbent's: kept before a candidate replaces it
        in_line = latest.get('version_id') == incumbent_id and latest.get('platform_version') == published.get('number')
        put_back = files.restore(incumbent_id, None if in_line else local_deployment(slot, entry, incumbent_id, built.get('local_workflow_sha256'), was, 'published'))
        if put_back:
            record['files_before'] = put_back
            trace('optimize %s: the files of the slot were not those of the published version %s: %s' % (slot, incumbent_id, '; '.join(put_back)))

        # 2. The builder that built the incumbent and this builder must be the same, or the difference is the candidate.
        try:
            rebuilt, _ = pipeline.build_workflow(spec, document, best_effort)
        except Exception as exc:
            raise Stop('stopped: this builder does not build the spec of the incumbent%s: %s' % ('' if best_effort else ' (a slot built with --best-effort needs it here too)', str(exc)[:300]))
        builder_changed = versioning.version_id(rebuilt) != incumbent_id
        if builder_changed and not rebuild:
            raise Stop('stopped: the builder has changed since the incumbent was built: it compiles the spec of %s to %s. A candidate would differ from the incumbent by more than '
                       'its change. Pass --rebuild to test this builder\'s build of the same spec as a candidate of its own' % (incumbent_id, versioning.version_id(rebuilt)))

        # 3. The incumbent's results, bound to its version; simulated once if they are not there.
        rows, _ = bound(evaluation.stored(incumbent_id), incumbent_id, workflow_id)
        missing = [c for c in needed if not policy.complete(rows.get(c))]
        if missing:
            stop = limits.exhausted(CREDITS_PER_CASE * len(missing), 0)
            if stop:
                raise Stop('stopped before the incumbent was evaluated: ' + stop)
            trace('optimize %s: the incumbent %s has no complete result for %d case(s); simulating them' % (slot, incumbent_id, len(missing)))
            before, problem = balance(), ''
            try:
                evaluation.run(incumbent_id, label(incumbent_id), missing)
            except Exception as exc:
                problem = ' (%s: %s)' % (type(exc).__name__, str(exc)[:200])
            except BaseException as exc:
                charge(before, CREDITS_PER_CASE * len(missing))
                done('interrupted while the incumbent was evaluated: %s. Nothing was deployed' % type(exc).__name__)
                raise
            charge(before, CREDITS_PER_CASE * len(missing))
            record['baseline'] = {'label': label(incumbent_id), 'cases': missing}
            rows, _ = bound(evaluation.stored(incumbent_id), incumbent_id, workflow_id)
            missing = [c for c in needed if not policy.complete(rows.get(c))]
            if missing:
                raise Stop('stopped: the incumbent has no complete result for: %s%s' % (', '.join(missing), problem))
        incumbent = {c: rows[c] for c in needed}
        numbered = ran_on(incumbent) == [published.get('number')]
        # Without named cases, the safety cases are those of the evaluation that the incumbent passes: each must pass
        # for a candidate too. One the incumbent fails cannot be demanded of every candidate; wrong bank records in
        # it reject a candidate all the same (policy.decide looks at the bank records of every case).
        safety = named or [c for c in marked if incumbent[c]['pass']]
        record['incumbent'].update(cases=policy.shown(incumbent), ran_on=ran_on(incumbent))
        record['safety_cases'] = safety
        if not safety:
            raise Stop('stopped: the incumbent passes none of the safety cases of the evaluation, and none was named; a candidate is never promoted without one')

        # 4. Diagnose, and make the candidate.
        tried, declined, changes = earlier(audit, incumbent_id)
        found = diagnosis.plan(incumbent, set(named) | set(marked), tried, declined)
        record['diagnosis'] = {k: found[k] for k in ('failing', 'addressable', 'needs_code_change', 'not_diagnosable', 'given_up')}
        if builder_changed:
            changed, workflow = spec, rebuilt
            why = {'kind': 'builder', 'hypothesis': 'the builder changed since the incumbent was built; the spec is the same', 'from': incumbent_id,
                   'from_builder_commit': built.get('builder_commit'), 'to': versioning.version_id(rebuilt)}
            if evaluation.stored(versioning.version_id(rebuilt)):
                raise Stop('stopped: this builder\'s build of the incumbent\'s spec (%s) was tested before and did not replace it. The change of the builder needs a decision by a person'
                           % versioning.version_id(rebuilt), modification=why)
        else:
            if not found['failing']:
                raise Stop('stopped: no case fails; nothing to improve')
            if not found['next']:
                raise Stop('stopped: no failed case can be addressed by a change of words: %d need a change of code, %d cannot be diagnosed, %d were tried'
                           % (len(found['needs_code_change']), len(found['not_diagnosable']), len(found['given_up'])))
            if client is None:
                raise Stop('stopped: no model was given to propose a change')
        stop = limits.exhausted(cost)
        if stop:
            raise Stop('stopped: ' + stop)
        limits.spend(candidates=1)
        if not builder_changed:
            case = found['next']
            record.update(diagnosed=case['id'], filed_as=case['kind'])
            try:
                changed, why = candidates.propose(spec, case, client, check, enforced, trace, usage, changes.get(case['id'], ()))
                workflow, _ = pipeline.build_workflow(changed, document, best_effort)
            except candidates.Rejected as exc:
                raise Stop('rejected before deployment: ' + str(exc), declined=exc.declined)
            except spend.BudgetExceeded as exc:
                raise Stop('stopped: the limit for paid model calls: %s' % exc)
            except Exception as exc:
                raise Stop('rejected before deployment: the candidate does not build: %s: %s' % (type(exc).__name__, str(exc)[:300]))
        candidate_id = versioning.version_id(workflow)
        record.update(modification=why, tested={'label': label(candidate_id), 'version_id': candidate_id, 'local_workflow_sha256': versioning.workflow_hash(workflow)})
        if candidate_id == incumbent_id:
            raise Stop('rejected before deployment: the candidate compiles to the incumbent itself')
        if evaluation.stored(candidate_id):
            raise Stop('rejected before deployment: this build (%s) was tested before; its results are not counted twice' % candidate_id)
        stop = limits.exhausted(cost, 0)
        if stop:
            raise Stop('stopped before deployment: ' + stop)
    except Stop as stop:
        return done(stop.status, **stop.more)
    except Exception as exc:
        return done('stopped: the step failed before anything was deployed: %s: %s' % (type(exc).__name__, str(exc)[:300]))

    # 5. Deploy as a new version of the same workflow. After this line, every way out goes through settle().
    before = balance()
    verdict, tested, now, interrupt = None, {}, was, None
    try:
        source = {'kind': 'optimization candidate', 'incumbent': incumbent_id, 'change': why.get('kind'), 'case': record.get('diagnosed')}
        pipeline.release(files, slot, changed, document, source, trace, best_effort, lambda: freya, registry)
        now = deployment.platform_versions(freya, workflow_id)
        record['tested']['platform_version'] = now['published']
        if (now['published'] or {}).get('number') in (None, published.get('number')):
            raise RuntimeError('the candidate was saved, but platform version %s is still the published one' % published.get('number'))
        got, _ = bound(evaluation.run(candidate_id, label(candidate_id)), candidate_id, workflow_id)
        tested = {c: got[c] for c in needed if c in got}
        record['tested']['ran_on'] = ran_on(tested)
        verdict = policy.decide(incumbent, tested, safety)
        if verdict['gate'] != 'evidence' and check_platform_version and numbered and ran_on(tested) != [now['published']['number']]:
            # The incumbent's own calls report the platform version they ran on; the candidate's must report theirs.
            verdict = dict(policy.decide({}, {}, None), reason='the calls of the candidate report platform version(s) %s, not version %s that was published for them'
                           % (ran_on(tested), now['published']['number']), missing=sorted(needed))
    except BaseException as exc:
        interrupt = None if isinstance(exc, Exception) else exc
        verdict = dict(policy.decide({}, {}, None), reason='the step failed before a decision: %s: %s' % (type(exc).__name__, str(exc)[:200]), missing=sorted(set(needed) - set(tested)))

    # 6. Carry the decision out, read it back, and put the files of the slot in line with what is published.
    record['tested']['cases'] = policy.shown(tested)
    record['decision'] = verdict
    try:
        row, state = settle(freya, slot, workflow_id, verdict['decision'], published['number'], (now['published'] or {}).get('number'), trace,
                            'candidate %s rejected at the %s gate: %s' % (candidate_id, verdict['gate'], verdict['reason']))
    except BaseException as exc:
        charge(before, cost)
        done('ROLLBACK NOT VERIFIED: %s: %s. The published version of %s must be checked by hand before anything else is run' % (type(exc).__name__, str(exc)[:300], slot))
        if not isinstance(exc, Exception):
            raise
        if interrupt is not None:
            raise interrupt
        return record
    record['published_after'] = state['published']
    unrestored = ''
    try:
        if row:
            files.append(store.HISTORY, row)
        if verdict['decision'] != policy.ACCEPT:
            record['files_after'] = files.restore(incumbent_id, local_deployment(slot, entry, incumbent_id, built.get('local_workflow_sha256'), state, 'rollback'))
    except Exception as exc:
        unrestored = '. THE FILES OF THE SLOT WERE NOT PUT BACK (%s: %s): they are the candidate\'s, the published version is the incumbent' % (type(exc).__name__, str(exc)[:200])
    charge(before, cost)
    how = 'interrupted (%s); ' % type(interrupt).__name__ if interrupt is not None else ''
    if verdict['decision'] == policy.ACCEPT:
        done(how + 'accepted: ' + verdict['reason'])
    else:
        done(how + 'rolled back at the %s gate: %s%s' % (verdict['gate'], verdict['reason'], unrestored))
    if interrupt is not None:
        raise interrupt
    return record


def run(*args, rounds=None, **kwargs):
    """Steps until nothing more can or may be done; the arguments are those of `step`. Returns the records.

    A step that accepted its candidate leaves it published, and the next step finds it as the incumbent, with the
    results of its own evaluation. A step that rolled back or refused a proposal has used one candidate of the
    limit; the next one diagnoses again and leaves out what was tried. Any other ending stops the run: a limit,
    nothing left to address, something that needs a person. `rounds` stops it earlier."""
    records = []
    while rounds is None or len(records) < rounds:
        records.append(step(*args, **kwargs))
        if not records[-1]['status'].startswith(CONTINUES):
            break
    return records
