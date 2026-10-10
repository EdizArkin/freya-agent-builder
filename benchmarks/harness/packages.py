"""Evaluation packages: the inputs an extraction may read, and the answer key it may not.

    python benchmarks/harness/packages.py list
    python benchmarks/harness/packages.py freeze      write MANIFEST.json from the files as they are
    python benchmarks/harness/packages.py verify      compare the files with MANIFEST.json

A package is a directory of benchmarks/packages/:

    package.json   title, form, tier, version, document order, where the tool API comes from, changelog
    input/         the brief documents (and openapi.json): the only files an extraction reads
    key/           rules.json and checks.json, covered by the freeze; reference.spec.json and
                   reference.fit.json, which belong to one state of the schema and may follow it

`inputs` reads input/ only and `key` reads key/ only, so that code which extracts cannot reach
an answer by accident. Tiers: development packages may be looked at as often as needed; the
selection package chooses once between frozen candidates; the final package is run once on
what was chosen. All packages here are synthetic and prepared by the author of the builder.
"""
import hashlib
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
BENCH = HERE.parent
ROOT = BENCH.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PACKAGES = BENCH / 'packages'
MANIFEST = PACKAGES / 'MANIFEST.json'
TIERS = ('development', 'selection', 'final')
FROZEN = ('rules.json', 'checks.json', 'bank.json')
REFERENCE = ('reference.spec.json', 'reference.fit.json')


def ids():
    return sorted(p.parent.name for p in PACKAGES.glob('*/package.json'))


def meta(pid):
    return json.loads((PACKAGES / pid / 'package.json').read_text(encoding='utf-8'))


def tier(pid):
    return meta(pid)['tier']


def inputs(pid):
    """(documents in order as (name, text), the tool API document). Reads input/ only."""
    info, folder = meta(pid), PACKAGES / pid / 'input'
    documents = [(name, (folder / name).read_text(encoding='utf-8')) for name in info['documents']]
    if info.get('openapi_from_kit'):
        # The kit's tool API is third-party material and stays out of the repository: the document
        # is the local kit file with this package's overlay applied.
        from benchmarks.harness import scenarios
        return documents, scenarios.tool_api(info['openapi_from_kit'])
    return documents, json.loads((folder / 'openapi.json').read_text(encoding='utf-8'))


def key(pid):
    """The answer key: rules, checks, the reference spec with its fit table, the bank's field names."""
    folder = PACKAGES / pid / 'key'
    need = lambda name: json.loads((folder / name).read_text(encoding='utf-8'))
    read = lambda name, default: need(name) if (folder / name).exists() else default
    checks = need('checks.json')
    return {'rules': need('rules.json')['rules'], 'defaults': checks.get('defaults') or {}, 'checks': checks['checks'],
            'spec': need('reference.spec.json'), 'fit': need('reference.fit.json'), 'bank': read('bank.json', {}),
            'mutations': read('mutations.json', {'mutations': []})['mutations']}


def digest(data):
    """sha256 of a file's text with one kind of line ending, so that a checkout cannot change it."""
    if isinstance(data, Path):
        data = data.read_bytes()
    return hashlib.sha256(data.replace(b'\r\n', b'\n')).hexdigest()


def hashes(pid):
    folder, info = PACKAGES / pid, meta(pid)
    documents, api = inputs(pid)
    return {'version': info['version'], 'tier': info['tier'],
            'inputs': {name: digest((folder / 'input' / name)) for name in sorted(p.name for p in (folder / 'input').iterdir())},
            'tool_api': digest(json.dumps(api, sort_keys=True).encode('utf-8')),
            'key': {name: digest(folder / 'key' / name) for name in FROZEN if (folder / 'key' / name).exists()},
            'reference': {name: digest(folder / 'key' / name) for name in REFERENCE if (folder / 'key' / name).exists()}}


def freeze():
    manifest = {'frozen_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'packages': {pid: hashes(pid) for pid in ids()}}
    MANIFEST.write_text(json.dumps(manifest, indent=1) + '\n', encoding='utf-8')
    return manifest


def verify(only=None):
    """Differences between the files and the manifest. Inputs and frozen key files must match exactly;
    a changed reference file is reported too, and is allowed only with a changelog entry and a new freeze."""
    if not MANIFEST.exists():
        return ['there is no MANIFEST.json: the packages are not frozen']
    frozen = json.loads(MANIFEST.read_text(encoding='utf-8'))['packages']
    bad = []
    for pid in ([only] if only else sorted(set(frozen) | set(ids()))):
        if pid not in frozen or pid not in ids():
            bad.append('%s: %s' % (pid, 'not in the manifest' if pid not in frozen else 'in the manifest but not on disk'))
            continue
        now = hashes(pid)
        for part in ('version', 'tier', 'tool_api'):
            if now[part] != frozen[pid][part]:
                bad.append('%s: %s changed since the freeze' % (pid, part))
        for part in ('inputs', 'key', 'reference'):
            for name in sorted(set(now[part]) | set(frozen[pid][part])):
                if now[part].get(name) != frozen[pid][part].get(name):
                    bad.append('%s: %s/%s changed since the freeze' % (pid, part, name))
    return bad


def main(argv=None):
    command = (argv if argv is not None else sys.argv[1:] or ['list'])[0]
    if command == 'freeze':
        for pid, entry in freeze()['packages'].items():
            print('%s v%s %-11s %d input file(s), %d key file(s)' % (pid, entry['version'], entry['tier'], len(entry['inputs']), len(entry['key'])))
        return 0
    if command == 'verify':
        bad = verify()
        for line in bad:
            print(line)
        print('the packages match the manifest' if not bad else '%d difference(s)' % len(bad))
        return 1 if bad else 0
    for pid in ids():
        info = meta(pid)
        print('%s  %-11s %-5s v%s  %s' % (pid, info['tier'], info['form'], info['version'], info['title']))
    return 0


if __name__ == '__main__':
    sys.exit(main())
