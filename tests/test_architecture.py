"""The shape of the code base: one pipeline, clean import boundaries, and the runtime embedded from its one source."""
import ast
import json
import unittest
from pathlib import Path

from builder.compiler import compiler
from builder.config import settings
from builder.runtime import runtime
from builder.spec.openapi import Api
from benchmarks.harness.fixture_spec import spec as fixture
from tests.support import OPENAPI

PACKAGE = settings.ROOT / 'builder'
# Which builder modules each module may import. The pipeline is the only place that knows everything.
ALLOWED = {
    'builder.runtime.runtime': set(),
    'builder.config.settings': set(),
    'builder.artifacts.store': set(),
    'builder.evaluation.simulation': set(),
    'builder.freya.client': set(),
    'builder.spec.models': {'builder.runtime.runtime'},
    'builder.spec.openapi': {'builder.spec.models'},
    'builder.validation.spec': {'builder.runtime.runtime', 'builder.spec.models'},
    'builder.validation.safety': {'builder.config.settings'},
    'builder.validation.contract': {'builder.spec.openapi'},
    'builder.validation.workflow': {'builder.validation.safety'},
    'builder.compiler.compiler': {'builder.runtime.runtime', 'builder.spec.models'},
    'builder.extraction.claude': {'builder.config.settings', 'builder.spec.models'},
    'builder.extraction.spend': set(),
    'builder.tracing.trace': {'builder.config.settings'},
    'builder.freya.versioning': {'builder.config.settings'},
    'builder.freya.deployment': {'builder.freya.versioning'},
    'builder.__main__': {'builder.pipeline', 'builder.optimization.cli'},
    # The optimization run is a product feature: it uses the builder's own machinery and is handed its evaluation.
    'builder.optimization.policy': set(),
    'builder.optimization.diagnosis': {'builder.optimization.policy', 'builder.optimization'},
    'builder.optimization.candidate': {'builder.extraction.claude', 'builder.spec.models', 'builder.extraction', 'builder.spec'},
    'builder.optimization.loop': {'builder.pipeline', 'builder.artifacts.store', 'builder.artifacts', 'builder.extraction.spend', 'builder.extraction', 'builder.freya.deployment',
                                  'builder.freya.versioning', 'builder.freya', 'builder.optimization.candidate', 'builder.optimization.diagnosis', 'builder.optimization.policy',
                                  'builder.optimization', 'builder.spec.openapi', 'builder.spec', 'builder.validation.spec', 'builder.validation', 'builder'},
    'builder.optimization.cli': {'builder.artifacts.store', 'builder.artifacts', 'builder.config.settings', 'builder.config', 'builder.extraction.claude', 'builder.extraction.spend',
                                 'builder.extraction', 'builder.freya.client', 'builder.freya', 'builder.optimization.loop', 'builder.optimization.policy', 'builder.optimization',
                                 'builder.tracing.trace', 'builder.tracing'},
}


def modules():
    found = {}
    for path in PACKAGE.rglob('*.py'):
        if path.name == '__init__.py' or '__pycache__' in path.parts:
            continue
        found['.'.join(path.relative_to(settings.ROOT).with_suffix('').parts)] = path
    return found


def imports(path):
    names = set()
    for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names.update(node.module + '.' + a.name for a in node.names)
    return names


class Boundaries(unittest.TestCase):
    def test_there_is_exactly_one_architecture(self):
        self.assertEqual(sorted(modules()), sorted(list(ALLOWED) + ['builder.pipeline']))
        self.assertFalse((PACKAGE / 'agent').exists())
        for name in ('legacy', 'docs', 'examples'):
            self.assertFalse((settings.ROOT / name).exists(), name)
        for path in list(modules().values()) + list((settings.ROOT / 'tests').glob('*.py')):
            if path.name != 'test_architecture.py':
                self.assertNotIn('builder.agent', path.read_text(encoding='utf-8'), path.name)

    def test_every_module_imports_only_what_its_layer_allows(self):
        known = set(modules())
        for name, path in modules().items():
            if name == 'builder.pipeline':
                continue
            used = imports(path) & known
            self.assertLessEqual(used, ALLOWED[name], name + ' imports ' + str(sorted(used - ALLOWED[name])))

    def test_no_import_cycles(self):
        known = modules()
        graph = {name: imports(path) & set(known) for name, path in known.items()}
        state = {}

        def visit(name, trail):
            if state.get(name) == 'done':
                return
            self.assertNotEqual(state.get(name), 'open', 'cycle: ' + ' -> '.join(trail + [name]))
            state[name] = 'open'
            for dep in graph[name]:
                visit(dep, trail + [name])
            state[name] = 'done'
        for name in graph:
            visit(name, [])

    def test_only_the_named_modules_can_reach_the_network_or_a_subprocess(self):
        # No module of the builder runs a subprocess. `importlib` alone loads the evaluation module an optimization run
        # is handed, by its name; `importlib.util` loads the kit.
        reach = {'anthropic': {'builder.extraction.claude'}, 'subprocess': set(), 'importlib.util': {'builder.freya.client'}, 'importlib': {'builder.optimization.cli'}}
        for name, path in modules().items():
            used = imports(path)
            for library, owners in reach.items():
                if name not in owners:
                    self.assertNotIn(library, used, name)
            for library in ('urllib.request', 'http.client', 'socket', 'requests', 'httpx', 'httpx2'):
                self.assertNotIn(library, used, name)
        self.assertNotIn('anthropic', {n.names[0].name for n in ast.parse(modules()['builder.extraction.claude'].read_text(encoding='utf-8')).body
                                       if isinstance(n, ast.Import)}, 'the SDK is imported only inside the call')

    def test_compiler_and_validation_never_touch_the_platform(self):
        for name in ('builder.compiler.compiler', 'builder.validation.spec', 'builder.validation.workflow', 'builder.validation.safety',
                     'builder.spec.models', 'builder.spec.openapi', 'builder.runtime.runtime'):
            used = imports(modules()[name])
            self.assertFalse({u for u in used if u.startswith(('builder.freya', 'builder.evaluation', 'builder.extraction', 'builder.pipeline'))}, name)

    def test_the_builder_never_imports_the_harness_or_the_tests_and_the_harness_never_imports_the_tests(self):
        """The builder is handed its evaluation: it names the module (settings.EVALUATION) and loads it when a run
        starts. The offline engine stand-in is the harness's own, so nothing that is not a test depends on tests/."""
        for name, path in modules().items():
            self.assertFalse({u for u in imports(path) if u.split('.')[0] in ('benchmarks', 'tests')}, name)
        for path in (settings.ROOT / 'benchmarks' / 'harness').glob('*.py'):
            self.assertFalse({u for u in imports(path) if u.split('.')[0] == 'tests'}, path.name)
        self.assertEqual(settings.EVALUATION, 'benchmarks.harness.evaluation')
        for name in ('freya_sim.py', 'fixture_spec.py'):
            self.assertFalse((settings.ROOT / 'tests' / name).exists(), name)
            self.assertTrue((settings.ROOT / 'benchmarks' / 'harness' / name).is_file(), name)


class EmbeddedRuntime(unittest.TestCase):
    """The compiler embeds builder/runtime/runtime.py as source text. Moving the file must not change what is embedded."""

    def test_the_compiler_reads_the_one_runtime_source(self):
        self.assertEqual(compiler.RUNTIME_SOURCE, Path(runtime.__file__))
        self.assertEqual(compiler.RUNTIME_SOURCE, PACKAGE / 'runtime' / 'runtime.py')
        self.assertEqual([p.name for p in PACKAGE.rglob('runtime*.py')], ['runtime.py'])

    def test_the_runtime_source_is_sandbox_compatible(self):
        tree = ast.parse(compiler.RUNTIME_SOURCE.read_text(encoding='utf-8'))
        top = [n for n in tree.body if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant))]
        self.assertTrue(all(isinstance(n, (ast.Import, ast.Assign, ast.FunctionDef)) for n in top))
        self.assertEqual(sorted(a.name for n in top if isinstance(n, ast.Import) for a in n.names), ['datetime', 'json', 're'])
        self.assertEqual([n for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)], [])
        source = compiler.RUNTIME_SOURCE.read_text(encoding='utf-8')
        self.assertNotIn('{{', source)
        self.assertNotIn('}}', source)
        self.assertNotIn('builder', source.split('"""', 2)[2])

    def test_each_snippet_embeds_its_entry_point_with_all_dependencies(self):
        cfg = compiler.runtime_cfg(fixture(True), Api(OPENAPI))
        functions, header = compiler._library()
        self.assertEqual(sorted(functions), sorted(n for n, v in vars(runtime).items() if callable(v) and getattr(v, '__module__', '') == runtime.__name__))
        for entry in ('setup', 'load_account', 'pre_identity', 'post_identity', 'post_account', 'pre_check', 'post_check', 'pre_promise', 'post_promise', 'pre_note',
                      'post_note', 'pre_callback', 'post_callback', 'pre_transfer', 'post_transfer', 'pre_close', 'post_close'):
            code = compiler.runtime_code(entry, entry + '(CFG, vars, config, result)', cfg)
            tree = ast.parse(code)
            wrapper = tree.body[0]
            self.assertEqual((type(wrapper), wrapper.name), (ast.FunctionDef, '_rt'))
            defined = {n.name for n in wrapper.body if isinstance(n, ast.FunctionDef)}
            self.assertIn(entry, defined)
            called = {n.id for n in ast.walk(wrapper) if isinstance(n, ast.Name) and n.id in functions}
            self.assertEqual(called - defined, set(), entry + ' misses a dependency')
            for name in defined:
                self.assertEqual(ast.dump(next(n for n in wrapper.body if isinstance(n, ast.FunctionDef) and n.name == name)),
                                 ast.dump(functions[name]), name + ' differs from the runtime source')

    def test_an_embedded_snippet_behaves_like_the_imported_runtime(self):
        cfg = compiler.runtime_cfg(fixture(), Api(OPENAPI))
        direct, embedded = {'now': '2026-10-14T11:00'}, {'now': '2026-10-14T11:00'}
        runtime.setup(cfg, direct)
        exec(compile(compiler.runtime_code('setup', 'setup(CFG, vars)', cfg), '<freya>', 'exec'), {'__builtins__': __builtins__}, {'vars': embedded})
        for variables in (direct, embedded):      # the id of the call is made from the clock and differs between two set-ups
            state = json.loads(variables['rt_state'])
            self.assertTrue(state.pop('run'))
            variables['rt_state'] = json.dumps(state)
        self.assertEqual(json.dumps(direct, sort_keys=True), json.dumps(embedded, sort_keys=True))
        self.assertIn('rt_calendar', embedded)

    def test_generated_code_with_a_template_marker_is_refused(self):
        with self.assertRaises(compiler.CompileError):
            compiler.runtime_code('setup', "setup(CFG, vars); x = {'a': {'b': 1}}", {})


if __name__ == '__main__':
    unittest.main()
