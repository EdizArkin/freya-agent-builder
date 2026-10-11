"""One command from a brief to a verified phone agent on Freya, with its export and its trace.

    python builder/build.py BRIEF TOOL_API [--amend BRIEF_B] [--suite public:A ...] [--export FILE] [--trace FILE]

This is the whole builder in one run, and it calls what it needs by itself: there is no flag to permit the model
or the platform (--plan prints what would be done and calls nothing).

    brief + tool API -> extraction (model) -> agent-spec/1 -> validation -> compiler -> workflow on Freya
        -> [self-tests -> diagnosis -> candidate (model) -> new version -> self-tests -> keep or roll back]*
        -> the published version is read back -> its export -> the trace

The part in brackets runs when suites of test customers are named (--suite): the optimization run of
builder/optimization/, within the limits given. A brief without test customers is built, deployed, verified and
exported; nothing is tested, and the trace says so.

What ends a run, and what it leaves:
- Every stage writes its line to the trace before the next one starts (tracing/runlog.py).
- The run has a deadline (--deadline-minutes, default 25 of the event's 30). The last --reserve-minutes of it
  are kept for the final read-back, the export and the trace: no simulation is started that could run into
  them, and no simulation is waited for beyond them.
- The export that is handed out is the one kept with the build that the platform reports as published, checked
  against that build's workflow. After a rollback the platform's own export is the definition saved last, not
  the published one, so it is never taken at face value. If the published version cannot be tied to a build,
  or its export is not there or differs, the run ends `incomplete` and hands out no export.
- Ctrl-C, or a file named STOP in the folder of the run, ends a run through the same path as any failure: a
  candidate that is published is rolled back first.

A brief without test customers (a hidden brief) is built from what can be checked without them:
- The model is called at most --max-model-calls times (4 without a suite), no call is started when too little
  time is left before the minutes kept for the end, and the run waits for one call at most
  --model-call-seconds. A call that does not come back is left behind and the run goes on; the request itself
  is ended only by the SDK's own timeout and may still be billed.
- If the extraction gives no valid spec (a call fails or times out, or the spec stays invalid after its
  repairs), one more extraction is made with the `single` strategy, when the time and the calls allow it.
  Nothing is published from a spec that does not pass every check.
- The coverage audit (extraction/coverage.py) checks whether the spec carries the mandatory rules of the
  documents and reports what it finds; the run publishes all the same. With --coverage block an omission
  that is firm stops the run before anything is published; --coverage off skips the audit.
- One version is published. Nothing is proposed or replaced without test customers.
- The trace and the report say what was checked, by kind: deterministic static checks, document audits by a
  model, validation by the platform, simulated customers. Each check is PASS, FAIL, SKIP, NOT TESTED or
  UNVERIFIED. A static check is never reported as a simulated customer, and no score is made up from them.

Exit code 0: an agent is published, verified and exported. 1: failed or incomplete. 130: interrupted.
"""
import argparse
import contextlib
import hashlib
import importlib
import json
import sys
import threading
import time
from pathlib import Path

from builder import pipeline
from builder.artifacts import store
from builder.config import settings
from builder.extraction import claude, coverage, spend
from builder.freya import client as freya_client
from builder.freya import deployment, versioning
from builder.optimization import loop, policy
from builder.spec.models import SpecError
from builder.spec.openapi import Api
from builder.tracing.runlog import RunLog
from builder.validation.spec import digest, validate

SUCCESS, INCOMPLETE, FAILED, INTERRUPTED = 'success', 'incomplete', 'failed', 'interrupted'

# Seconds a model call needs to be worth starting. On 2026-10-11 an extraction took 53 and 84 s, an audit 6 and
# 12 s, a revision 36 s (the traces of the two demo runs).
LEAST_SECONDS = 45              # any call
FALLBACK_SECONDS = 120          # a second extraction
COVERAGE_SECONDS = 90           # the coverage audit
HIDDEN_MODEL_CALLS = 4          # the model calls of a run without test customers, unless --max-model-calls says otherwise

# What a run checked, by kind, and what a check can come to.
STATIC, AUDITS, PLATFORM, BEHAVIOR = 'static', 'audit', 'platform', 'simulation'
KINDS = {STATIC: 'Deterministic static checks', AUDITS: 'LLM-based document audits', PLATFORM: 'Platform validation', BEHAVIOR: 'Live behavioral simulation'}
PASS, FAIL, SKIP, NOT_TESTED, UNVERIFIED = 'PASS', 'FAIL', 'SKIP', 'NOT TESTED', 'UNVERIFIED'
AUDIT, COVERAGE, UNSUPPORTED = 'audit of the high-risk content', 'coverage of the mandatory rules', 'rules the extractor could not express'
READBACK, EXPORTED, CUSTOMERS = 'read-back of the published version', 'export is the published build', 'customer simulation'
CHECKS = ((STATIC, 'credential check of the spec'), (STATIC, 'spec validation against the tool API'), (STATIC, 'compilation'), (STATIC, 'credential check of the workflow'),
          (STATIC, 'workflow validation'), (STATIC, 'tool-contract validation'), (AUDITS, AUDIT), (AUDITS, COVERAGE), (AUDITS, UNSUPPORTED),
          (PLATFORM, 'dry run'), (PLATFORM, 'publication'), (PLATFORM, 'export read from the platform'), (PLATFORM, READBACK), (PLATFORM, EXPORTED), (BEHAVIOR, CUSTOMERS))


class Incomplete(Exception):
    """The run cannot hand out a verified agent, and says why."""


def within(seconds, call, *args):
    """What call(*args) returns, if it returns within `seconds`; TimeoutError if it does not.

    The call runs in a thread of its own, and after `seconds` the caller goes on without it. That is all this
    guarantees: the call is not interrupted, it is left behind, and it ends when it ends or with the process."""
    box = {}

    def work():
        try:
            box['value'] = call(*args)
        except BaseException as exc:
            box['error'] = exc
    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    end = time.time() + seconds
    while thread.is_alive() and time.time() < end:
        thread.join(min(0.5, max(0.01, end - time.time())))       # short waits, so that Ctrl-C is seen
    if thread.is_alive():
        raise TimeoutError('no reply within %.0f s; the run goes on without it (the request is not interrupted and may still be billed)' % seconds)
    if 'error' in box:
        raise box['error']
    return box['value']


class Watched:
    """The model client of a run. It decides whether a call is made and how long the run waits for it:
    - none after `until`, the time the run keeps for its end, and none that has too little time left;
    - at most `max_calls` calls, of which `held` are kept back for a later stage;
    - the run waits for one call at most `call_seconds`, and never beyond `until` (see `within`).
    A call that is refused or fails is written to the trace (a call that succeeds is written by the code that
    made it, with its purpose). `sdk`, the client that talks to the API, is told how long it has."""

    def __init__(self, client, log, until=None, call_seconds=None, max_calls=None, sdk=None):
        self.client, self.log, self.model = client, log, getattr(client, 'model', None)
        self.max_tokens = getattr(client, 'max_tokens', None)
        self.until, self.call_seconds, self.max_calls, self.sdk = until, call_seconds, max_calls, sdk
        self.calls, self.held = 0, 0

    def refused(self, seconds=None):
        """Why no call that needs `seconds` (LEAST_SECONDS unless given) can be started now, or '' if it can."""
        seconds = LEAST_SECONDS if seconds is None else seconds
        if self.max_calls and self.calls + self.held >= self.max_calls:
            return 'the limit of %d model call(s) of this run is reached: %d made%s' % (self.max_calls, self.calls, ', %d kept for a later stage' % self.held if self.held else '')
        if self.until is not None and self.until - time.time() < seconds:
            return '%.0f s are left for model calls before the time kept for the end of the run, and a call needs %d' % (max(0.0, self.until - time.time()), seconds)
        return ''

    def complete(self, system, messages):
        why = self.refused()
        if why:
            self.log('LLM call not made | %s | %s' % (self.model, why))
            raise spend.BudgetExceeded(why)
        self.calls += 1
        limits = [v for v in (self.call_seconds, None if self.until is None else self.until - time.time()) if v is not None]
        wait = min(limits) if limits else None
        if self.sdk is not None and wait is not None:
            self.sdk.timeout, self.sdk.retries = wait, 1
        try:
            return self.client.complete(system, messages) if wait is None else within(wait, self.client.complete, system, messages)
        except Exception as exc:
            self.log('LLM call failed | %s | %s: %s' % (self.model, type(exc).__name__, str(exc)[:300]))
            raise


class Evidence:
    """What a run checked and what came of each check: one row per check, written to the trace as it is made.
    A check has a kind (KINDS) and a status. PASS and FAIL are for a check that was made; SKIP for one that was
    left out, with the reason; UNVERIFIED for one whose result cannot be relied on; NOT TESTED for one the run
    never came to. The rows are the report: nothing is added up from them."""

    def __init__(self, log):
        self.rows, self.log = [], log

    def add(self, kind, check, status, detail=''):
        self.rows = [row for row in self.rows if (row['kind'], row['check']) != (kind, check)] + [{'kind': kind, 'check': check, 'status': status, 'detail': str(detail)}]
        self.log('evidence | %s | %s | %s%s' % (KINDS[kind], check, status, ' | ' + str(detail) if detail else ''))

    @contextlib.contextmanager
    def stage(self, kind, check, unsure=False):
        """Around one check of a build: PASS if it returns, FAIL with what it raised. `unsure` is for a step
        that may have happened although it raised (a save that did not answer): UNVERIFIED."""
        try:
            yield
        except Exception as exc:
            self.add(kind, check, UNVERIFIED if unsure else FAIL, '%s: %s' % (type(exc).__name__, str(exc)[:300]))
            raise
        except BaseException:
            self.add(kind, check, UNVERIFIED, 'interrupted')
            raise
        self.add(kind, check, PASS)

    def status(self, kind, check):
        return next((row['status'] for row in self.rows if (row['kind'], row['check']) == (kind, check)), None)

    def close(self, why):
        """Every check the run did not come to is NOT TESTED, and says so. Returns the rows in the order of CHECKS."""
        for kind, check in CHECKS:
            if self.status(kind, check) is None:
                self.add(kind, check, NOT_TESTED, why)
        order = {pair: number for number, pair in enumerate(CHECKS)}
        self.rows.sort(key=lambda row: order.get((row['kind'], row['check']), len(order)))
        return self.rows

    def summary(self):
        parts = []
        for kind in KINDS:
            rows = [row for row in self.rows if row['kind'] == kind]
            parts.append('%s: %s' % (KINDS[kind], ', '.join('%d %s' % (sum(row['status'] == status for row in rows), status)
                                                             for status in (PASS, FAIL, UNVERIFIED, SKIP, NOT_TESTED) if any(row['status'] == status for row in rows)) or 'none'))
        return ' | '.join(parts)


def arguments(argv=None, provider=None):
    first = argparse.ArgumentParser(add_help=False)
    first.add_argument('--evaluation', default=settings.EVALUATION)
    named = first.parse_known_args(argv)[0].evaluation
    provider = provider or importlib.import_module(named)
    p = argparse.ArgumentParser(prog='python builder/build.py', description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('brief', help='the brief: one Markdown file')
    p.add_argument('tool_api', help="the brief's tool API file (OpenAPI)")
    p.add_argument('--amend', action='append', default=[], help='a later document that amends the brief (Brief B on Brief A); repeat, in order')
    p.add_argument('--slot', help='the name of the agent: its workflow on Freya and its folder under --out. Default: the file name of the brief')
    p.add_argument('--new', action='store_true', help='stop if the slot already has a workflow: this run must create its own')
    p.add_argument('--run-id', help='the id of this run in the trace and in every file it writes. Default: time and slot')
    p.add_argument('--trace', help='the .log file of this run. Default: <out>/<slot>/traces/<run id>.log')
    p.add_argument('--export', help='where the export of the final published version is copied to, e.g. submission/hidden-1.workflow.json')
    p.add_argument('--results', help='a folder for the report of this run: <run id>.json and <run id>.md')
    p.add_argument('--strategy', choices=claude.STRATEGIES, default='audit', help='how the spec is extracted (default: audit, the configuration selected in M15)')
    p.add_argument('--prompt-version', choices=sorted(claude.PROMPT_VERSIONS), default=claude.PROMPT_VERSION)
    p.add_argument('--model', help='the builder\'s model; default BUILDER_LLM_MODEL or ' + settings.DEFAULT_MODEL)
    p.add_argument('--max-tokens', type=int, default=settings.MAX_TOKENS)
    p.add_argument('--strict', action='store_true', help='fail the build when the extractor reports policy it could not express, instead of building with a warning')
    p.add_argument('--spec', help='reuse an extracted spec instead of calling the model for it')
    p.add_argument('--no-deploy', action='store_true', help='extract and compile only; nothing is sent to Freya')
    p.add_argument('--max-dollars', type=float, default=5.0, help='the limit of this run for paid model calls (default 5)')
    p.add_argument('--max-model-calls', type=int, help='model calls this run may start. Default: %d without --suite, no limit with one; 0 is no limit' % HIDDEN_MODEL_CALLS)
    p.add_argument('--model-call-seconds', type=float, default=240.0, help='how long the run waits for one model call (default 240); never beyond the minutes kept for the end')
    p.add_argument('--coverage', choices=('block', 'report', 'off'), help='the audit of whether the spec carries the mandatory rules of the brief. block: an omission that is firm '
                   'stops the run before anything is published. report: publish and report it. off: no audit. Default: report without --suite, off with one')
    p.add_argument('--deadline-minutes', type=float, default=25.0, help='the whole run, self-tests included (default 25; the event allows 30)')
    p.add_argument('--reserve-minutes', type=float, default=4.0, help='kept at the end for the read-back, the export and the trace (default 4)')
    p.add_argument('--evaluation', default=settings.EVALUATION, help='the module that simulates test customers and keeps the results (default: %(default)s)')
    p.add_argument('--safety-case', action='append', default=[], help='a case that must pass for a candidate; repeat. Default: the safety cases the incumbent passes')
    p.add_argument('--max-candidates', type=int, default=policy.LIMITS['candidates'], help='candidates the optimization may deploy (default %(default)s)')
    p.add_argument('--max-credits', type=float, default=policy.LIMITS['credits'], help='Freya credits the self-tests may use, by projection and by the balance (default %(default)s)')
    p.add_argument('--rounds', type=int, help='stop the optimization after this many steps')
    p.add_argument('--no-optimize', action='store_true', help='with --suite: test the built agent once and propose nothing')
    p.add_argument('--repeat', type=int, default=1, help='test the final version this many times in all (default 1)')
    p.add_argument('--no-platform-version-check', action='store_true', help=argparse.SUPPRESS)
    p.add_argument('--plan', action='store_true', help='print what the run would do and call nothing')
    p.add_argument('--out', default=str(settings.OUTPUTS))
    p.add_argument('--kit', default=str(settings.KIT), help='starter kit folder holding freya.py and tools/run_cases.py')
    p.add_argument('--env-file', action='append', default=[], help='extra env file with API keys')
    if hasattr(provider, 'add_arguments'):
        provider.add_arguments(p)
    args = p.parse_args(argv)
    args.slot = store.build_id(args.slot or '-plus-'.join(Path(b).stem for b in [args.brief] + args.amend)).lower()
    args.run = args.slot                    # the evaluation keeps its records in the folder of the slot: one track, one folder
    args.run_id = store.build_id(args.run_id or '%s-%s' % (time.strftime('%Y%m%d-%H%M%S'), args.slot))
    return args, provider


def counts(workflow):
    """(steps, tools the phone model can call) of a workflow."""
    nodes = workflow.get('nodes') or {}
    return len(nodes), sum(len(node.get('tools') or []) for node in nodes.values())


def score(cases):
    """'7 of 10' and the failed cases, from a vector as policy.shown writes it."""
    failed = sorted(cid for cid, state in (cases or {}).items() if state != 'pass')
    return '%d of %d' % (len(cases or {}) - len(failed), len(cases or {})), failed


def report(result):
    """The report of a run as text: the first version, every candidate, the final version, each with what was observed."""
    out = ['# Builder run %s' % result['run_id'], '', 'Status: **%s**%s' % (result['status'], ' (%s)' % result['reason'] if result.get('reason') else ''), '',
           '| | |', '| --- | --- |', '| Brief | %s |' % ', '.join(result['briefs']), '| Tool API | %s |' % result['tool_api'], '| Builder commit | %s |' % result['builder_commit'],
           '| Workflow | %s |' % result.get('workflow_id'), '| Agent | %s |' % result.get('agent_id'), '| Final build | %s |' % result.get('final_build'),
           '| Published platform version | %s |' % (result.get('published') or {}).get('number'), '| Export | %s |' % result.get('export'), '| Trace | %s |' % result['trace'],
           '| Elapsed | %s s |' % result.get('seconds'), '| Model calls | %s, $%s |' % (result.get('model_calls'), result.get('model_dollars')), '| Freya credits | %s |' % result.get('credits'), '']
    if not result.get('versions'):
        out += ['No self-test was run: %s.' % (result.get('untested') or 'none was asked for'), '']
    for version in result.get('versions') or []:
        got, failed = score(version['cases'])
        out += ['## %s: %s' % (version['role'], version['version_id']), '', '%s case(s) pass, one run each.%s' % (got, ' Failed: ' + ', '.join('%s (%s)' % (c, version['cases'][c]) for c in failed) if failed else ''),
                ''] + (['Change: %s' % version['change'], ''] if version.get('change') else []) + (['Decision: %s' % version['decision'], ''] if version.get('decision') else [])
    for number, cases in enumerate(result.get('repetitions') or [], 1):
        out += ['Repetition %d of the final version: %s case(s) pass.' % (number, score(cases)[0])]
    if result.get('repetitions'):
        always = sorted(c for c in result['repetitions'][0] if all(rep.get(c) == 'pass' for rep in result['repetitions']))
        out += ['', 'Pass in all %d repetitions: %d of %d.' % (len(result['repetitions']), len(always), len(result['repetitions'][0])), '']
    for entry in result.get('needs_code_change') or []:
        out += ['Needs a change of code, not of words: %s (%s)' % (entry['id'], entry['why'])]
    out += ['', 'Every figure above is what this run observed. A target that was not reached is not reported as reached.', '']
    made = result.get('provenance') or {}
    if made:
        out += ['## Provenance', '', '| What | SHA-256 |', '| --- | --- |']
        out += ['| Brief %d of %d: %s | %s |' % (number, len(made.get('briefs') or []), brief['file'], brief['sha256']) for number, brief in enumerate(made.get('briefs') or [], 1)]
        out += ['| Tool API: %s | %s |' % ((made.get('tool_api') or {}).get('file'), (made.get('tool_api') or {}).get('sha256')), '| Validated spec | %s |' % made.get('spec_sha256'),
                '| Compiled workflow | %s |' % made.get('workflow_sha256'), '',
                'Spec from: %s. Builder commit %s; a change of the code that was not committed does not show in it.' % (made.get('spec_from'), result['builder_commit'] or 'unknown'), '']
    if result.get('evidence'):
        out += ['## Evaluation', '', 'What this run checked, by kind. A static check says that the spec and the workflow are well-formed and agree with the tool API. A document audit is the reading of '
                'a model, checked by code as far as its quotations go. Neither says how the agent behaves in a call: only a simulated customer does, and only for the cases that were run. '
                'No score is made from these rows.', '']
        if result.get('first_build') and result.get('final_build') and result['first_build'] != result['final_build']:
            out += ['The static checks and the audits below are those of the first build of this run, %s. A candidate passes the same static checks before it is deployed.' % result['first_build'], '']
        for kind, title in KINDS.items():
            out += ['### ' + title, '', '| Check | Status | Detail |', '| --- | --- | --- |']
            out += ['| %s | %s | %s |' % (row['check'], row['status'], row['detail'].replace('|', '/').replace('\n', ' ')) for row in result['evidence'] if row['kind'] == kind] + ['']
    audit = result.get('coverage')
    if audit:
        out += ['## Mandatory rules of the documents that are not confirmed as covered', '',
                '%d requirement(s) were listed by the model. The list is the model\'s reading: a rule it did not list is not here. %d of %d lines of the documents are quoted by no entry.' % (
                    audit['summary']['requirements'], audit['summary']['lines_unquoted'], audit['summary']['lines']), '']
        out += ['- **%s**%s: %s (%s). Source, %s: "%s"' % (row['status'], ', firm' if row['blocking'] else '', row['requirement'], row['why'], row['document'] or 'not found in the documents',
                                                       row['source'].replace('\n', ' ')[:300]) for row in audit['rows'] if row['status'] != coverage.COVERED] or ['None.']
        out += ['']
    return '\n'.join(out)


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def extraction(args, briefs, openapi_text, check, model, log, describe, system):
    """The spec of a run: extracted with the strategy that was asked for and, if that gives no valid spec, once
    more with `single`, when the time and the calls allow a whole extraction. Returns (spec, the strategy that
    made it, the outcome of the audit, why the fallback was made or ''). Raises what the last attempt raised.
    Nothing is retried beyond that."""
    audit = {}
    model.held = 1 if model.max_calls and model.max_calls > 1 else 0        # while the first extraction runs, one call is kept for the second
    try:
        return claude.run(args.strategy, briefs, openapi_text, model, check, log, digest=describe, system=system, outcome=audit), args.strategy, audit, ''
    except Exception as exc:
        first = exc
    finally:
        model.held = 0
    why = 'the spec stayed invalid after its repairs' if isinstance(first, SpecError) else '%s: %s' % (type(first).__name__, str(first)[:200])
    refused = model.refused(FALLBACK_SECONDS)
    if refused:
        log('extraction fallback | not made | %s | the %s extraction gave no spec: %s' % (refused, args.strategy, why))
        raise first
    log('extraction fallback | strategy single, once | the %s extraction gave no spec: %s' % (args.strategy, why))
    try:
        spec = claude.run('single', briefs, openapi_text, model, check, log, system=system)
    except Exception as exc:
        log('extraction fallback | no valid spec | %s: %s' % (type(exc).__name__, str(exc)[:300]))
        raise
    log('extraction fallback | a spec that passes validation')
    return spec, 'single', {'status': SKIP, 'detail': 'the fallback extraction makes no audit'}, why


def covered(mode, spec, briefs, openapi_text, check, model, log, evidence, system):
    """The coverage audit of a run, within the calls and the time it has. Returns (spec, report or None): the
    spec may be the one revised for reported omissions. An audit that is not made, or fails, is reported as
    SKIP and changes nothing: a valid spec is not lost to it."""
    if mode == 'off':
        evidence.add(AUDITS, COVERAGE, SKIP, 'not asked for (--coverage off, the default of a run with test customers)')
        return spec, None
    why = model.refused(COVERAGE_SECONDS)
    if why:
        evidence.add(AUDITS, COVERAGE, SKIP, why)
        return spec, None
    try:
        spec, report = coverage.audit(spec, briefs, openapi_text, model, check, log, system, refused=model.refused)
    except Exception as exc:
        evidence.add(AUDITS, COVERAGE, SKIP, 'the audit call failed (%s: %s)' % (type(exc).__name__, str(exc)[:200]))
        return spec, None
    told, counts = report['summary'], report['summary']['counts']
    detail = '%d requirement(s) listed by the model: %d covered, %d overridden, %d missing (%d firm), %d ambiguous, %d unverified | %d of %d lines of the documents quoted by no entry%s' % (
        told['requirements'], counts[coverage.COVERED], counts[coverage.OVERRIDDEN], counts[coverage.MISSING], told['blocking'], counts[coverage.AMBIGUOUS], counts[coverage.UNVERIFIED],
        told['lines_unquoted'], told['lines'], ' | the spec was revised once for the omissions' if report['revised'] else '')
    if not report['readable'] or not report['rows']:
        evidence.add(AUDITS, COVERAGE, UNVERIFIED, 'the reply of the audit %s; it proves nothing' % ('could not be read' if not report['readable'] else 'lists no requirement'))
    else:
        evidence.add(AUDITS, COVERAGE, FAIL if counts[coverage.MISSING] else UNVERIFIED if counts[coverage.AMBIGUOUS] or counts[coverage.UNVERIFIED] else PASS, detail)
    return spec, report


def standing(slot, files, load, registry, log):
    """After a run that hands out nothing: what the slot's workflow publishes now, read from the platform and
    not changed. An earlier version that was published stays published when a later run fails before its save."""
    known = versioning.known(slot, registry)
    if not known or not known.get('workflow_id'):
        log('published now | the slot %s has no workflow on Freya: nothing is published for it' % slot)
        return None
    try:
        published = (deployment.platform_versions(load(), known['workflow_id']) or {}).get('published')
        build = loop.published_build(files, published) if published else None
    except Exception as exc:
        log('published now | workflow %s | cannot be read: %s: %s' % (known['workflow_id'], type(exc).__name__, str(exc)[:200]))
        return None
    kept = bool(build) and (files.version(build['version_id']) / store.EXPORT).is_file()
    log('published now | workflow %s | platform version %s (%s) | build %s | its export is %s' % (
        known['workflow_id'], (published or {}).get('number'), (published or {}).get('id'), (build or {}).get('version_id') or 'not a build of this slot\'s history',
        'kept at %s' % (files.version(build['version_id']) / store.EXPORT) if kept else 'not kept'))
    return {'workflow_id': known['workflow_id'], 'published': published, 'build': (build or {}).get('version_id')}


def main(argv=None, client=None, platform=None, evaluation=None, registry=versioning.REGISTRY):
    """`client`, `platform` and `evaluation` replace the model client, the function that loads the Freya client and
    the evaluation module: a test passes fakes, and nothing else differs from a real run."""
    settings.safe_console()
    args, provider = arguments(argv, evaluation)
    suites = list(getattr(args, 'suite', None) or [])
    if args.plan:
        print('plan for %s (run %s): %s the spec from %s with the tool API %s; validate and compile it; %s%s. Nothing was called.' % (
            args.slot, args.run_id, 'reuse' if args.spec else 'extract (model, strategy %s)' % args.strategy, ', '.join([args.brief] + args.amend), args.tool_api,
            'stop there' if args.no_deploy else 'deploy it to the workflow of slot "%s" on Freya, read the published version back and export it' % args.slot,
            '' if args.no_deploy or not suites else '; before that, test it with %s and %s within %d candidate(s), %s credits and %s minutes' % (
                ', '.join(suites), 'propose nothing' if args.no_optimize else 'improve it', args.max_candidates, args.max_credits, args.deadline_minutes)))
        return 0
    files = store.BuildStore(args.out, args.slot)
    log = RunLog(args.trace or files.path('traces') / (args.run_id + '.log'), args.run_id)
    evidence = Evidence(log)
    started = time.time()
    deadline = started + 60.0 * args.deadline_minutes
    usable = deadline - 60.0 * args.reserve_minutes
    # A run without test customers is a hidden brief: few model calls, one version, and the coverage audit, which reports.
    max_calls = args.max_model_calls if args.max_model_calls is not None else (None if suites else HIDDEN_MODEL_CALLS)
    mode = args.coverage or ('off' if suites else 'report')
    result = {'run_id': args.run_id, 'slot': args.slot, 'briefs': [args.brief] + args.amend, 'tool_api': args.tool_api, 'builder_commit': versioning.builder_commit(),
              'started_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'trace': str(log.path), 'status': FAILED, 'command': 'python builder/build.py ' + ' '.join(argv if argv is not None else sys.argv[1:])}
    log('run start | run %s | slot %s | builder commit %s' % (args.run_id, args.slot, result['builder_commit'] or 'unknown'))
    log('inputs | brief %s | tool API %s' % (', '.join(result['briefs']), args.tool_api))
    log('configuration | extraction %s, prompt %s | deadline %s min, %s min kept for the end | model calls up to $%s | self-tests: %s | up to %d candidate(s), %s credits | repeat %d' % (
        'from the file ' + args.spec if args.spec else args.strategy, args.prompt_version, args.deadline_minutes, args.reserve_minutes, args.max_dollars,
        ', '.join(suites) or 'none', args.max_candidates, args.max_credits, args.repeat))
    log('limits | model calls: %s, each waited for at most %.0f s, none started in the last %s min of the %s | coverage audit: %s | versions published without test customers: one' % (
        'at most %d' % max_calls if max_calls else 'no limit on their number', args.model_call_seconds, args.reserve_minutes, args.deadline_minutes, mode))
    code, model, load = 1, None, None
    try:
        # 1. configuration and inputs
        settings.load_env(settings.env_files(args.env_file, args.kit))
        briefs = [(Path(b).name, Path(b).read_text(encoding='utf-8')) for b in [args.brief] + args.amend]
        document = json.loads(Path(args.tool_api).read_text(encoding='utf-8'))
        result['provenance'] = {'briefs': [{'file': str(b), 'sha256': sha256(b), 'bytes': Path(b).stat().st_size} for b in [args.brief] + args.amend],
                                'tool_api': {'file': str(args.tool_api), 'sha256': sha256(args.tool_api), 'bytes': Path(args.tool_api).stat().st_size}}
        for number, brief in enumerate(result['provenance']['briefs'], 1):
            log('provenance | brief %d of %d | %s | sha256 %s | %d bytes' % (number, len(briefs), brief['file'], brief['sha256'], brief['bytes']))
        log('provenance | tool API | %s | sha256 %s | %d bytes' % (args.tool_api, result['provenance']['tool_api']['sha256'], result['provenance']['tool_api']['bytes']))
        api = Api(document)
        best_effort = not args.strict
        known = versioning.known(args.slot, registry)
        if args.new and known:
            raise Incomplete('the slot %s already has the workflow %s; --new asks for a run that creates its own' % (args.slot, known.get('workflow_id')))
        load = platform or (lambda: freya_client.load(args.kit, patiently=True))
        if known and not suites and not args.no_deploy:
            log('policy | the slot %s already has the workflow %s: this run publishes a new version of it without test customers. The version published now stays on the platform and can be '
                'published again; --new makes a run refuse a slot that has a workflow' % (args.slot, known.get('workflow_id')))
        sdk = None
        if client is None and not (args.spec and (args.no_optimize or not suites)):
            if not claude.sdk_installed():
                raise Incomplete('the Anthropic SDK is not installed for this Python')
            sdk = claude.AnthropicClient(args.model, args.max_tokens, fallback=False)
            client = spend.Budget(sdk, args.max_dollars, files.path('spend.%s.json' % args.run_id))
        model = Watched(client, log, usable, args.model_call_seconds, max_calls, sdk) if client is not None else None

        # 2. the spec: extracted by the model and repaired until it validates; then audited against the documents
        audited = None
        if args.spec:
            spec = json.loads(Path(args.spec).read_text(encoding='utf-8'))
            source = {'kind': 'reused spec', 'file': str(args.spec), 'run_id': args.run_id}
            log('spec: reused from %s (no model call)' % args.spec)
            for check in (AUDIT, COVERAGE):
                evidence.add(AUDITS, check, SKIP, 'the spec was reused from a file, not extracted in this run')
        else:
            source = {'kind': 'extraction', 'strategy': args.strategy, 'prompt_version': args.prompt_version, 'prompt_digest': claude.prompt_digest(version=args.prompt_version),
                      'model': model.model, 'run_id': args.run_id}
            log('extraction | strategy %s | prompt %s (digest %s) | model %s' % (args.strategy, args.prompt_version, source['prompt_digest'], model.model))
            text, system = json.dumps(document, separators=(',', ':')), claude.system_prompt(version=args.prompt_version)
            check = lambda s: validate(s, api, best_effort)
            spec, used, audit, fallback = extraction(args, briefs, text, check, model, log, lambda one, other=None: digest(one, api, other), system)
            if fallback:
                source.update(strategy=used, strategy_asked=args.strategy, fallback=fallback)
            evidence.add(AUDITS, AUDIT, audit.get('status') or SKIP, audit.get('detail') or 'the %s strategy makes no audit' % used)
            spec, audited = covered(mode, spec, briefs, text, check, model, log, evidence, system)
            if audited:
                result['coverage'] = audited
                if audited['revised']:
                    source['revised_after_coverage_audit'] = True
                firm = [row for row in audited['rows'] if row['blocking']]
                if firm and mode == 'block':
                    held = files.path('runs') / (args.run_id + '.unpublished-spec.json')
                    held.parent.mkdir(parents=True, exist_ok=True)
                    held.write_text(json.dumps(spec, indent=1, ensure_ascii=False), encoding='utf-8')
                    raise Incomplete('the coverage audit found %d mandatory requirement(s) of the documents that the spec does not carry, each with a figure that occurs nowhere in the spec: %s. '
                                     'Nothing was built or published; the spec is kept at %s (--coverage report publishes such a spec and reports the omissions)' % (
                                         len(firm), '; '.join(row['requirement'][:120] for row in firm[:3]), held))
        source['inputs'] = dict(result['provenance'])
        unsupported = [str(rule) for rule in spec.get('unsupported') or []]
        for rule in unsupported:
            log('NOT ENFORCED | the extractor reports policy the builder cannot express: %s' % rule)
        evidence.add(AUDITS, UNSUPPORTED, UNVERIFIED if unsupported else PASS, '%d rule(s) are in the rule text only and nothing enforces them: %s' % (len(unsupported), ' | '.join(unsupported)[:400])
                     if unsupported else 'the extractor reported none; this is its own account')

        # 3. validate, compile, validate, archive; 4. the platform
        workflow, built, deployed = pipeline.release(files, args.slot, spec, document, source, log, best_effort, None if args.no_deploy else load, registry, evidence.stage)
        steps, tools = counts(workflow)
        result.update(final_build=built['version_id'], first_build=built['version_id'], steps=steps, tools=tools)
        result['provenance'].update(spec_sha256=built['spec_sha256'], workflow_sha256=built['local_workflow_sha256'], spec_from=(
            'the file %s' % source['file'] if args.spec else 'extraction, strategy %s%s, prompt %s (digest %s), model %s' % (
                source['strategy'], ' (asked for: %s; fallback because: %s)' % (source['strategy_asked'], source['fallback']) if source.get('fallback') else '',
                source['prompt_version'], source['prompt_digest'], source['model'])))
        log('provenance | spec sha256 %s | workflow sha256 %s | build %s | spec from %s' % (built['spec_sha256'], built['local_workflow_sha256'], built['version_id'], result['provenance']['spec_from']))
        if deployed is None:
            log('build | %s | %d steps, %d tools | not deployed (--no-deploy)' % (built['version_id'], steps, tools))
            raise Incomplete('built and not deployed, as asked (--no-deploy)')
        result.update(workflow_id=deployed['workflow_id'], agent_id=deployed['agent_id'])
        log('freya | workflow %s | agent %s | workflow name "%s ..." | build %s | platform version %s | %d steps | %d tools | %s' % (
            deployed['workflow_id'], deployed['agent_id'], args.slot, built['version_id'], (deployed.get('platform') or {}).get('published', {}).get('number', deployed.get('platform_version')),
            steps, tools, deployed['operation']))
        freya = load()

        # 5. self-tests and optimization, when test customers are named and there is time
        records, limits = [], None
        if not suites:
            result['untested'] = 'no suite of test customers was named for this brief'
            log('self-test | none: %s' % result['untested'])
        elif (usable - time.time()) < 120:
            result['untested'] = 'no time left before the deadline'
            log('self-test | none: %s' % result['untested'])
        else:
            args.deadline = usable
            evaluate = provider.Evaluation(args)
            folder = Path(args.out) / args.slot
            limits = policy.Limits(folder / ('limits.%s.json' % args.run_id), {'candidates': args.max_candidates, 'credits': args.max_credits, 'minutes': max(1.0, (usable - time.time()) / 60.0)})
            credits = (lambda: freya_client.credits(freya).get('creditsLeft')) if hasattr(freya, 'api') else None
            records = loop.run(args.slot, document, evaluate, freya, files, limits, folder / 'optimization.jsonl', client=None if args.no_optimize else model,
                               safety_cases=args.safety_case, best_effort=best_effort, credits=credits, check_platform_version=not args.no_platform_version_check,
                               registry=registry, trace=log, rounds=1 if args.no_optimize else args.rounds)
            for number, record in enumerate(records, 1):
                log('optimization | step %d | %s | %s | remaining: %d candidate(s), %.2f credits, %.0f min' % (
                    number, 'case %s (%s)' % (record['diagnosed'], record.get('filed_as')) if record.get('diagnosed') else 'no candidate', record['status'],
                    limits.limits['candidates'] - limits.state['candidates'], limits.limits['credits'] - limits.state['credits'], max(0.0, (usable - time.time()) / 60.0)))
            result.update(versions=versions(records), needs_code_change=((records[-1].get('diagnosis') or {}).get('needs_code_change') or []) if records else [],
                          credits=limits.state['credits'], steps_taken=[r['status'] for r in records])

        # 6. the final version: what the platform publishes, which build that is, and that build's export
        def stop(check, why, status=FAIL):
            evidence.add(PLATFORM, check, status, why)
            raise Incomplete(why)

        try:
            state = deployment.platform_versions(freya, deployed['workflow_id'])
        except Exception as exc:
            evidence.add(PLATFORM, READBACK, UNVERIFIED, 'the versions of the workflow could not be read: %s: %s' % (type(exc).__name__, str(exc)[:200]))
            raise
        published = (state or {}).get('published')
        if not published:
            stop(READBACK, 'the platform lists no published version of workflow %s' % deployed['workflow_id'])
        final = loop.published_build(files, published)
        if not final:
            stop(READBACK, 'the published platform version %s (%s) is not a build of this slot\'s history' % (published.get('number'), published.get('id')))
        evidence.add(PLATFORM, READBACK, PASS, 'platform version %s (%s) is published and is build %s' % (published.get('number'), published.get('id'), final['version_id']))
        result.update(final_build=final['version_id'], published=published)
        files.restore(final['version_id'])
        kept = files.read(store.EXPORT, final['version_id'])
        if kept is None:
            stop(EXPORTED, 'build %s has no export kept with it' % final['version_id'])
        changed, _ = deployment.departures(files.read(store.WORKFLOW, final['version_id']), kept.get('workflow'))
        if changed or kept.get('workflow_id', deployed['workflow_id']) != deployed['workflow_id']:
            stop(EXPORTED, 'the export kept with build %s is not that build on this workflow: %s' % (final['version_id'], '; '.join(changed[:3]) or kept.get('workflow_id')))
        if any(r['status'].startswith('ROLLBACK NOT VERIFIED') or 'NOT PUT BACK' in r['status'] for r in records):
            stop(READBACK, 'a rollback of this run was not verified; the published version must be checked by hand', UNVERIFIED)
        evidence.add(PLATFORM, EXPORTED, PASS, 'every value of the workflow of build %s is in its export, unchanged' % final['version_id'])
        if args.repeat > 1 and records and limits is not None:
            result['repetitions'] = repetitions(args, evaluate, final['version_id'], deployed['workflow_id'], limits, credits, usable, records, log)
        export = files.version(final['version_id']) / store.EXPORT
        if args.export:
            Path(args.export).parent.mkdir(parents=True, exist_ok=True)
            Path(args.export).write_bytes(export.read_bytes())
            export = Path(args.export)
        result.update(export=str(export), status=SUCCESS)
        log('final | build %s | platform version %s (%s) is published, read back from Freya | export %s | trace %s' % (
            final['version_id'], published.get('number'), published.get('id'), export, log.path))
        last = next((v for v in reversed(result.get('versions') or []) if v['kept']), None)
        if last:
            got, failed = score(last['cases'])
            log('final | self-tests of the published version: %s case(s) pass%s | scope: %s, one run each' % (got, ' | failed: ' + ', '.join(failed) if failed else '', ', '.join(suites)))
        code = 0
    except KeyboardInterrupt as exc:
        result.update(status=INTERRUPTED, reason=str(exc) or 'interrupted')
        log('INTERRUPTED | %s' % result['reason'])
        code = 130
    except Incomplete as exc:
        result.update(status=INCOMPLETE, reason=str(exc))
        log('INCOMPLETE | %s' % exc)
    except SpecError as exc:
        result.update(status=FAILED, reason='the spec is not valid')
        for problem in exc.problems:
            log('SPEC ERROR | ' + problem)
    except Exception as exc:
        result.update(status=FAILED, reason='%s: %s' % (type(exc).__name__, str(exc)[:500]))
        log('FAILED | %s' % result['reason'])
    # What the run leaves: the published version when the run hands out none, and the account of what was checked.
    if result['status'] in (FAILED, INCOMPLETE) and load is not None and not args.no_deploy:
        result['published_now'] = standing(args.slot, files, load, registry, log)
    last = next((v for v in reversed(result.get('versions') or []) if v['kept']), None)
    if last:
        got, failed = score(last['cases'])
        evidence.add(BEHAVIOR, CUSTOMERS, FAIL if failed else PASS, '%s case(s) of %s pass on build %s, one run each%s' % (
            got, ', '.join(suites), last['version_id'], '; not passing: ' + ', '.join('%s (%s)' % (c, last['cases'][c]) for c in failed) if failed else ''))
    elif result.get('untested'):
        evidence.add(BEHAVIOR, CUSTOMERS, NOT_TESTED, '%s: no customer was simulated, and how the agent behaves in a call is not known from this run' % result['untested'])
    result['evidence'] = evidence.close('the run ended before it (%s)' % result['status'] if result['status'] != SUCCESS else 'this run has no result of it')
    log('evaluation | %s' % evidence.summary())
    if evidence.status(BEHAVIOR, CUSTOMERS) == NOT_TESTED:
        log('evaluation | behavior in a call: NOT TESTED. The static checks and the audits say that the spec is well-formed and matches the documents as far as they go; they are no test of a call')
    usage = getattr(client, 'usage', None) or []
    result.update(seconds=round(time.time() - started), ended_at=time.strftime('%Y-%m-%dT%H:%M:%S%z'), model_calls=log.model_calls, model_calls_started=model.calls if model is not None else 0,
                  model_dollars=spend.dollars(usage) if usage else 0.0)
    if time.time() > deadline:
        log('DEADLINE | the run took %.1f min, more than the %.0f it was given' % ((time.time() - started) / 60.0, args.deadline_minutes))
    for folder in [files.path('runs')] + ([Path(args.results)] if args.results else []):
        folder.mkdir(parents=True, exist_ok=True)
        (folder / (args.run_id + '.json')).write_text(json.dumps(result, indent=1, ensure_ascii=False, default=str), encoding='utf-8')
        (folder / (args.run_id + '.md')).write_text(report(result), encoding='utf-8')
    log('run end | status %s | %d s | %d model call(s), $%.2f | workflow %s | agent %s | build %s | export %s' % (
        result['status'], result['seconds'], result['model_calls'], result['model_dollars'], result.get('workflow_id'), result.get('agent_id'), result.get('final_build'), result.get('export')))
    log.close()
    return code


def versions(records):
    """The versions a run tested, in order, each with what was observed: the first one, then every candidate."""
    out = []
    for number, record in enumerate(records):
        incumbent = record.get('incumbent') or {}
        if number == 0 and incumbent.get('cases'):
            out.append({'role': 'initial version', 'version_id': incumbent.get('version_id'), 'cases': incumbent['cases'], 'kept': True})
        tested = record.get('tested') or {}
        if tested.get('cases') is not None and record.get('decision'):
            change = record.get('modification') or {}
            accepted = record['decision']['decision'] == policy.ACCEPT
            if accepted:
                for earlier in out:
                    earlier['kept'] = False
            out.append({'role': 'candidate %d (%s)' % (sum(1 for v in out if v['role'].startswith('candidate')) + 1, 'accepted' if accepted else 'rejected, rolled back'),
                        'version_id': tested.get('version_id'), 'cases': tested['cases'], 'kept': accepted,
                        'change': '%s, for case %s: %s' % (change.get('field') or change.get('kind'), record.get('diagnosed'), change.get('hypothesis')),
                        'decision': '%s at the %s gate: %s' % (record['decision']['decision'], record['decision']['gate'], record['decision']['reason'])})
    return out


def repetitions(args, evaluate, version_id, workflow_id, limits, credits, usable, records, log):
    """Test the final version again, up to --repeat runs in all, while the credits and the time allow a whole run.
    Returns one vector per run, the first being the run the decision rested on."""
    def balance():
        try:
            value = credits() if credits else None
        except Exception:
            return None
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None

    final = next((v for v in reversed(versions(records)) if v['kept']), None)
    out = [final['cases']] if final else []
    cases = evaluate.cases()
    for number in range(2, args.repeat + 1):
        most = len(cases) * loop.CREDITS_PER_CASE
        stop = limits.exhausted(most, 0) or ('no time left before the deadline' if usable - time.time() < 180 else '')
        if stop:
            log('repetition %d | not run: %s' % (number, stop))
            break
        before = balance()
        rows, _ = loop.bound(evaluate.run(version_id, '%s-r%d' % (loop.label(version_id), number)), version_id, workflow_id)
        after = balance()
        limits.spend(credits=max(0.0, before - after) if before is not None and after is not None else most)
        out.append(policy.shown({c: rows.get(c) or {} for c in cases}))
        log('repetition %d | %s case(s) pass' % (number, score(out[-1])[0]))
    return out


if __name__ == '__main__':
    raise SystemExit(main())
