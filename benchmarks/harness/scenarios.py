"""Benchmark definitions: loading, checking, and turning one scenario into a simulated call.

Nothing here talks to the network. The engine and the bank are the offline stand-ins of
tests/freya_sim.py, which copy what live calls showed on 2026-10-10. A pass in this harness
says what the generated runtime does with a given sequence of tool calls. It says nothing
about what a phone model will say or which tool calls it will choose.
"""
import copy
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BENCH = HERE.parent
ROOT = BENCH.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from builder.pipeline import build_workflow  # noqa: E402
from tests.freya_sim import Bank, Sim  # noqa: E402

KIT = ROOT / 'local_freya' / 'starter_kit'
EDGE_CASES = BENCH / 'edge_cases'
SYNTHETIC = BENCH / 'synthetic_briefs'
EXPECTED = BENCH / 'expected' / 'ledger_assertions.yaml'
MATRIX = BENCH / 'requirements' / 'coverage_matrix.md'

TIERS = ('runtime', 'scripted', 'live_only')
MODES = ('offline', 'live', 'both', 'specification')
STEP_KINDS = ('call', 'together', 'faults', 'check')
GAP_KINDS = ('defect', 'unenforced', 'schema', 'platform')
DEFAULT_VARIABLES = {'now': '2026-10-14T11:00', 'title': 'Ms', 'first_name': 'Ada', 'last_name': 'Okafor'}
CUSTOMER_ID = 'c_benchmark'


def load(path):
    """Read a definition file. The files are YAML written in its JSON subset, so the standard
    library reads them; with PyYAML installed any YAML is accepted."""
    text = Path(path).read_text(encoding='utf-8')
    try:
        return json.loads(text)
    except ValueError as first:
        try:
            import yaml
        except ImportError:
            raise ValueError('%s is not JSON-compatible YAML (%s). Install PyYAML to use other YAML syntax.' % (path, first))
        return yaml.safe_load(text)


def scenario_files():
    return sorted(EDGE_CASES.glob('*.yaml')) + sorted(SYNTHETIC.glob('*.scenarios.yaml'))


def load_scenarios():
    scenarios = []
    for path in scenario_files():
        document = load(path)
        for scenario in document['scenarios']:
            scenario = dict(scenario)
            scenario.setdefault('category', document.get('category'))
            scenario.setdefault('brief', document.get('brief'))
            scenario['file'] = path.relative_to(BENCH).as_posix()
            scenarios.append(scenario)
    return scenarios


def load_expected():
    return load(EXPECTED)['assertions']


def matrix_ids():
    return set(re.findall(r'^\| ([A-EP]-\d+[a-z]?) \|', MATRIX.read_text(encoding='utf-8'), flags=re.M))


def definition_problems(scenarios, expected):
    """Everything wrong with the definitions themselves, before any scenario runs."""
    bad, seen, known = [], set(), matrix_ids()
    for s in scenarios:
        sid = s.get('id', '?')
        if sid in seen:
            bad.append(sid + ': duplicate id')
        seen.add(sid)
        for key in ('id', 'title', 'category', 'brief', 'tier', 'mode', 'requirements', 'customer', 'behavior', 'spoken', 'strategy'):
            if key not in s:
                bad.append('%s: missing %s' % (sid, key))
        if s.get('tier') not in TIERS:
            bad.append('%s: tier must be one of %s' % (sid, ', '.join(TIERS)))
        if s.get('mode') not in MODES:
            bad.append('%s: mode must be one of %s' % (sid, ', '.join(MODES)))
        if s.get('known_gap') and s.get('gap_kind') not in GAP_KINDS:
            bad.append('%s: a known gap needs gap_kind, one of %s' % (sid, ', '.join(GAP_KINDS)))
        if s.get('tier') == 'live_only' and s.get('steps'):
            bad.append(sid + ': a live_only scenario has no offline steps')
        if s.get('tier') != 'live_only' and not s.get('steps'):
            bad.append(sid + ': an executable scenario needs steps')
        if s.get('mode') in ('live', 'both') and not (s.get('customer') or {}).get('public_case'):
            bad.append(sid + ': a live scenario must use the account of a public practice case')
        for step in s.get('steps') or []:
            if len([k for k in STEP_KINDS if k in step]) != 1:
                bad.append('%s: each step needs exactly one of %s' % (sid, ', '.join(STEP_KINDS)))
        for requirement in s.get('requirements') or []:
            if requirement not in known:
                bad.append('%s: requirement %s is not in the coverage matrix' % (sid, requirement))
        if sid not in expected:
            bad.append(sid + ': no entry in expected/ledger_assertions.yaml')
    for sid in expected:
        if sid not in seen:
            bad.append(sid + ': expected entry without a scenario')
    return bad


# ---------------------------------------------------------------- specs and tool APIs

def tool_api(brief):
    """The kit's tool API, with the overlay of a synthetic brief applied to a copy."""
    document = json.loads((KIT / 'tools' / 'openapi.json').read_text(encoding='utf-8'))
    overlay_path = SYNTHETIC / ('brief-%s.api.yaml' % brief.lower())
    if not overlay_path.exists():
        return document
    overlay = load(overlay_path)
    body = lambda tool: document['paths']['/' + tool]['post']['requestBody']['content']['application/json']['schema']['properties']
    product = document['paths']['/get_account']['post']['responses']['200']['content']['application/json']['schema']['properties']['products']['items']['properties']
    if overlay.get('identity_fields'):
        body('verify_identity')['field']['enum'] = list(overlay['identity_fields'])
    if overlay.get('queues'):
        body('transfer_to_agent')['queue']['enum'] = list(overlay['queues'])
    if overlay.get('note_reasons'):
        body('add_note')['reason']['enum'] = list(overlay['note_reasons'])
    if overlay.get('product_types'):
        product['type']['enum'] = list(overlay['product_types'])
    for name, schema in (overlay.get('product_fields') or {}).items():
        product[name] = copy.deepcopy(schema)
    for name in overlay.get('remove_product_fields') or []:
        product.pop(name, None)
    return document


def spec_for(brief, overrides=None):
    """The spec a brief's scenarios run against, and where it came from."""
    overrides = overrides or {}
    if brief in overrides:
        return load(overrides[brief]), str(overrides[brief])
    if brief in ('A', 'B'):
        from tests.fixture_spec import spec as fixture
        return fixture(brief == 'B'), 'tests/fixture_spec.py (hand-written)'
    path = SYNTHETIC / ('brief-%s.spec.yaml' % brief.lower())
    return load(path), path.relative_to(ROOT).as_posix() + ' (hand-written reference)'


# ---------------------------------------------------------------- customers

def public_case(case_id):
    cases = json.loads((KIT / 'public-test-set.json').read_text(encoding='utf-8'))
    return [c for c in cases if c['id'] == case_id][0]


def customer_of(scenario):
    """variables, identity, products and faults of the scenario's customer."""
    customer = scenario['customer']
    if customer.get('public_case'):
        case = public_case(customer['public_case'])
        faults = {f['tool']: f['fail_first_n'] for f in case.get('faults') or []}
        return dict(case['variables']), dict(case['identity']), copy.deepcopy(case['products']), faults
    variables = dict(DEFAULT_VARIABLES, **(customer.get('variables') or {}))
    return variables, dict(customer['identity']), copy.deepcopy(customer['products']), dict(customer.get('faults') or {})


def resolve(value, products, identity=None):
    """Replace "$p1.past_due" by that field of the first product and "$identity.phone_last4" by
    that security answer, anywhere in a structure. Scenarios on a public practice account use
    these so that no value of the kit's test set is copied into this repository."""
    if isinstance(value, str):
        m = re.fullmatch(r'\$p(\d+)\.(\w+)', value)
        if m:
            return products[int(m.group(1)) - 1][m.group(2)]
        m = re.fullmatch(r'\$identity\.(\w+)', value)
        return (identity or {})[m.group(1)] if m else value
    if isinstance(value, list):
        return [resolve(v, products, identity) for v in value]
    if isinstance(value, dict):
        return {k: resolve(v, products, identity) for k, v in value.items()}
    return value


# ---------------------------------------------------------------- the simulated call

class Call(Sim):
    """The offline engine as the benchmark drives it. Calls of one message run side by side, each on
    its own copy of the variables, as live calls showed; see tests/freya_sim.py for what is known
    and what is assumed about the merge."""

    def call(self, name, args, time_passes=True):
        result = self._api(self._find(name), args)
        self.advance()
        return result.get('data') if isinstance(result.get('data'), dict) else {}

    def together(self, calls, arrival='reversed', merge='all', apply='message'):
        return Sim.together(self, *calls, arrival=arrival, merge=merge, apply=apply)


_WORKFLOWS = {}


def workflow_for(brief, overrides=None):
    key = (brief, json.dumps(overrides or {}, sort_keys=True, default=str))
    if key not in _WORKFLOWS:
        spec, source = spec_for(brief, overrides)
        workflow, _ = build_workflow(spec, tool_api(brief))
        _WORKFLOWS[key] = (workflow, source)
    return _WORKFLOWS[key]


def start(scenario, overrides=None):
    """A fresh bank and a fresh call for one scenario."""
    workflow, source = workflow_for(scenario['brief'], overrides)
    variables, identity, products, faults = customer_of(scenario)
    bank = Bank(CUSTOMER_ID, identity, products, faults)
    return Call(copy.deepcopy(workflow), bank, variables), bank, source
