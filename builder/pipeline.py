"""One command from brief to phone agent.

    python -m builder --brief brief-a.md --tool-spec openapi.json --slot brief-a --live-extract --deploy

Without flags nothing leaves this machine. --live-extract permits the Anthropic
call, --deploy permits creating/updating the workflow on Freya, --test runs the
kit's practice customers against it as one named simulation. Every run writes
outputs/<slot>/trace.txt and archives its spec and workflow under their version id.
"""
import argparse
import json
import time
from pathlib import Path

from builder.artifacts import store
from builder.compiler.compiler import CompileError, compile_workflow
from builder.config import settings
from builder.evaluation import simulation
from builder.extraction import claude
from builder.freya import client as freya_client
from builder.freya import deployment, versioning
from builder.spec.models import SpecError
from builder.spec.openapi import Api
from builder.tracing.trace import Trace
from builder.validation import contract
from builder.validation.safety import leaks
from builder.validation.spec import digest, validate
from builder.validation.workflow import check_workflow


def build_workflow(spec, document, best_effort=False):
    """Validate the spec, compile it, validate the workflow. Returns (workflow, report).

    Raises SpecError when the spec cannot be executed faithfully and CompileError
    when the generated workflow breaks a platform rule.
    """
    api = Api(document)
    warnings = validate(spec, api, best_effort)
    workflow, report = compile_workflow(spec, api)
    problems = check_workflow(workflow)
    if problems:
        raise CompileError('; '.join(problems))
    # A compiled tool that departs from the tool API would send requests the bank does not expect: not built.
    # The runtime checks every outgoing request again (runtime.send).
    departures = contract.check_tools(workflow, api)
    if departures:
        raise CompileError('the compiled tools do not match the tool API: ' + '; '.join(departures))
    return workflow, dict({'warnings': warnings, 'contract': contract.document_notes(api)}, **report)


def arguments(argv=None):
    p = argparse.ArgumentParser(prog='python -m builder', description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--brief', action='append', required=True, help='brief file; repeat in order, later files amend earlier ones')
    p.add_argument('--tool-spec', required=True, help="the brief's OpenAPI tool file")
    p.add_argument('--slot', help='name of the agent; default: last brief file name')
    p.add_argument('--out', default=str(settings.OUTPUTS))
    p.add_argument('--spec', help='reuse an already extracted spec file instead of calling the model')
    p.add_argument('--live-extract', action='store_true', help='permit the Anthropic API call')
    p.add_argument('--strategy', choices=claude.STRATEGIES, default='single', help='how the spec is extracted: one call (single), one call and a targeted audit, or two calls and an adjudication (double)')
    p.add_argument('--prompt-version', choices=sorted(claude.PROMPT_VERSIONS), default=claude.PROMPT_VERSION, help='version of the extraction prompt')
    p.add_argument('--model', help='extraction model; default BUILDER_LLM_MODEL or ' + settings.DEFAULT_MODEL)
    p.add_argument('--max-tokens', type=int, default=settings.MAX_TOKENS)
    p.add_argument('--best-effort', action='store_true', help='compile even if the extractor reports policy it could not express')
    p.add_argument('--deploy', action='store_true', help='permit creating/updating and publishing the workflow on Freya')
    p.add_argument('--test', help='after deploy, run the kit practice customers of this brief letter, e.g. A')
    p.add_argument('--kit', default=str(settings.KIT), help='starter kit folder holding freya.py and tools/run_cases.py')
    p.add_argument('--env-file', action='append', default=[], help='extra env file with API keys')
    return p.parse_args(argv)


def main(argv=None, client=None):
    """`client` replaces the extraction model client, for a run behind a spending limit or a replay."""
    a = arguments(argv)
    slot = a.slot or Path(a.brief[-1]).stem
    files = store.BuildStore(a.out, slot)
    trace = Trace(files.path(store.TRACE))
    started = time.time()
    trace('build start | slot %s | briefs %s | tool api %s' % (slot, ', '.join(a.brief), a.tool_spec))
    code = 1
    try:
        # 1. configuration and inputs
        settings.load_env(settings.env_files(a.env_file, a.kit))
        briefs = [(Path(b).name, Path(b).read_text(encoding='utf-8')) for b in a.brief]
        document = json.loads(Path(a.tool_spec).read_text(encoding='utf-8'))
        api = Api(document)

        # 2. the spec: reused, or extracted by the model and repaired until it validates
        if a.spec:
            spec = json.loads(Path(a.spec).read_text(encoding='utf-8'))
            source = {'kind': 'reused spec', 'file': str(a.spec)}
            trace('spec: reused from %s (no model call)' % a.spec)
        elif a.live_extract:
            model = client or claude.AnthropicClient(a.model, a.max_tokens)
            check = lambda candidate: validate(candidate, api, a.best_effort)
            source = {'kind': 'extraction', 'strategy': a.strategy, 'prompt_version': a.prompt_version,
                      'prompt_digest': claude.prompt_digest(version=a.prompt_version), 'model': model.model}
            trace('extraction strategy: %s | prompt %s (digest %s)' % (a.strategy, a.prompt_version, source['prompt_digest']))
            spec = claude.run(a.strategy, briefs, json.dumps(document, separators=(',', ':')), model, check, trace,
                              digest=lambda one, other=None: digest(one, api, other), system=claude.system_prompt(version=a.prompt_version))
        else:
            raise SystemExit('No spec source: pass --live-extract to call the model, or --spec FILE to reuse one.')
        if leaks(spec):
            raise SpecError(['credential leak in the spec at ' + leak for leak in leaks(spec)])
        trace('spec written: %s' % files.write_json(store.SPEC, spec))

        # 3. validate, compile, validate
        workflow, report = build_workflow(spec, document, a.best_effort)
        workflow_path = files.write_json(store.WORKFLOW, workflow)
        for warning in report['warnings']:
            trace('warning: ' + warning)
        for line in report['contract']:
            trace('tool API contract: ' + line)
        trace('compiled and locally validated: %s | %d bytes | nodes %s' % (workflow_path, report['bytes'], ', '.join(report['nodes'])))
        built = versioning.build_record(slot, spec, workflow, source)
        files.write_json(store.BUILD, built)
        trace('version %s archived: %s' % (built['version_id'], files.archive(built['version_id'], spec, workflow, built)))

        # 4. the platform, only when asked
        if a.deploy:
            record = deployment.deploy(freya_client.load(a.kit), slot, workflow, files.path(store.EXPORT), trace)
            files.write_json(store.DEPLOYMENT, record)
            if a.test:
                cases = json.loads((Path(a.kit) / 'public-test-set.json').read_text(encoding='utf-8'))
                simulation.run_practice_cases(freya_client.load_runner(a.kit), record['agent_id'], a.test, cases, files.path(store.EVALUATION), trace,
                                              label=slot, version=built['version_id'])
        elif a.test:
            raise SystemExit('--test needs --deploy')
        code = 0
    except SpecError as exc:
        for problem in exc.problems:
            trace('SPEC ERROR: ' + problem)
    except CompileError as exc:
        trace('COMPILE ERROR: ' + str(exc))
    except SystemExit as exc:
        trace('STOPPED: ' + str(exc))
    except Exception as exc:
        trace('FAILED: %s: %s' % (type(exc).__name__, str(exc)[:2000]))
    trace('build end | %s | %.0f s' % ('ok' if code == 0 else 'failed', time.time() - started))
    return code
