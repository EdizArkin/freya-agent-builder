"""Explicit Freya operations through the local official standard-library client."""
import argparse
from copy import deepcopy
from dataclasses import asdict, dataclass
import importlib.util
import json
import os
from pathlib import Path
import re
from urllib.parse import urlparse

from builder.compiler.validate import parse_workflow, validate_workflow


class IntegrationError(ValueError):
    """Messages never include API responses, credentials or workflow content."""


def load_workflow(path):
    try:
        workflow = parse_workflow(Path(path).read_text(encoding='utf-8'))
        if not validate_workflow(workflow).valid:
            raise ValueError()
        return workflow
    except (OSError, ValueError, RecursionError):
        raise IntegrationError('Local workflow validation failed.') from None


def official_client(path):
    """Import only when an external operation was explicitly requested."""
    if not os.environ.get('FREYA_API_KEY'):
        raise IntegrationError('FREYA_API_KEY is required.')
    url = urlparse(os.environ.get('FREYA_API_URL', 'https://staging-app.freyavoice.ai'))
    if url.scheme != 'https' or not url.hostname or url.username or url.password:
        raise IntegrationError('FREYA_API_URL must be an HTTPS origin without credentials.')
    try:
        spec = importlib.util.spec_from_file_location('_official_freya', Path(path))
        client = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(client)
        return client
    except Exception:
        raise IntegrationError('Cannot load the trusted local official client.') from None


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,160}', value):
        raise IntegrationError('Invalid identifier in request or response.')
    return value


@dataclass(frozen=True)
class SavedWorkflow:
    workflow_id: str
    agent_id: str
    version_id: str
    version_number: int


def private_write(path, data):
    """Artifacts are restricted to ignored outputs/, without overwriting files."""
    root = Path('outputs').resolve()
    target = Path(path).resolve()
    if root not in target.parents or target.name == '.gitkeep':
        raise IntegrationError('Artifacts must be written beneath outputs/.')
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open('x', encoding='utf-8') as stream:
        json.dump(data, stream, sort_keys=True, indent=2, allow_nan=False)


class FreyaAdapter:
    def __init__(self, client, *, allow_external=False, dry_run_only=True):
        self.client = client
        self.allow_external = allow_external
        self.dry_run_only = dry_run_only
        self.events = []

    def _call(self, method, path, body=None, **params):
        if not self.allow_external:
            raise IntegrationError('External operations require explicit opt-in.')
        if self.dry_run_only and not (method == 'POST' and path == '/api/v2/workflow'
                                     and params == {'dryRun': 'true'}):
            raise IntegrationError('Dry-run-only mode forbids this operation.')
        try:
            result = self.client.api(method, path, body, **params)
            if isinstance(result, dict) and result.get('success') is False:
                raise ValueError()
        except Exception:
            self.events.append({'operation': method, 'status': 'failed'})
            raise IntegrationError('Freya operation failed; response content suppressed.') from None
        self.events.append({'operation': method, 'status': 'completed'})
        return result

    def _workspace(self):
        # Require explicit workspace selection rather than the client's first-workspace fallback.
        return identifier(os.environ.get('FREYA_WORKSPACE_ID'))

    def _prepared(self, workflow):
        if not validate_workflow(workflow).valid:
            raise IntegrationError('Local workflow validation failed.')
        value = deepcopy(workflow)
        model = self.client.AGENT_LLM
        config = value.setdefault('agent_config', {})
        if not isinstance(config, dict) or not isinstance(config.get('llmConfig', {}), dict):
            raise IntegrationError('Invalid agent configuration.')
        llm = config.setdefault('llmConfig', {})
        if any(k in llm and llm[k] != model[k] for k in ('provider', 'model')):
            raise IntegrationError('Workflow conflicts with organizer phone-agent model.')
        llm.update(model)
        self._no_credentials(value)
        return value

    @staticmethod
    def _no_credentials(value):
        text = json.dumps(value, allow_nan=False)
        secrets = [v for k, v in os.environ.items()
                   if re.search(r'(KEY|TOKEN|PASSWORD|SECRET)$', k, re.I)]
        if any(s and s in text for s in secrets) or re.search(r'(?i)fak_[a-z0-9_-]+|bearer\s+[a-z0-9]', text):
            raise IntegrationError('Artifact contains credential-like content.')
        def check(item):
            if isinstance(item, dict):
                for key, child in item.items():
                    if re.sub(r'[^a-z]', '', key.lower()) in (
                            'authorization', 'apikey', 'accesstoken', 'password', 'secret', 'token'):
                        raise IntegrationError('Credential fields must not be embedded in workflows.')
                    check(child)
            elif isinstance(item, list):
                for child in item:
                    check(child)
            elif isinstance(item, str) and '://' in item:
                parsed = urlparse(item)
                if parsed.username or parsed.password:
                    raise IntegrationError('URLs must not contain credentials.')
        check(value)

    def dry_run(self, workflow, name='builder validation'):
        self._call('POST', '/api/v2/workflow', {'name': name, 'workflow': self._prepared(workflow)}, dryRun='true')
        # The dry-run payload is undocumented; never print or persist it.

    def _get(self, workflow_id):
        result = self._call('GET', '/api/v2/workflow/' + identifier(workflow_id), workspaceId=self._workspace())
        value = result.get('workflow', result)
        if value.get('id') != workflow_id:
            raise IntegrationError('Workflow response identity mismatch.')
        identifier(value.get('agentId'))
        return value

    def _versions(self, workflow_id):
        result = self._call('GET', '/api/v2/versions/workflow/' + identifier(workflow_id), workspaceId=self._workspace())
        versions = result.get('versions', result)
        if not isinstance(versions, list) or any(not isinstance(v, dict) for v in versions):
            raise IntegrationError('Unsupported versions response.')
        for v in versions:
            identifier(v.get('id'))
            if type(v.get('versionNumber')) is not int:
                raise IntegrationError('Unsupported version number.')
        if len({v['id'] for v in versions}) != len(versions):
            raise IntegrationError('Duplicate version identifiers.')
        return versions

    def save(self, workflow, *, name='builder workflow', workflow_id=None):
        if self.dry_run_only or not self.allow_external:
            raise IntegrationError('Saving requires external opt-in and write mode.')
        self._workspace()  # Fail before any write if configuration is incomplete.
        value = self._prepared(workflow)
        self.dry_run(value, name)
        before = self._versions(workflow_id) if workflow_id else []
        if workflow_id:
            self._call('PUT', '/api/v2/workflow/' + identifier(workflow_id), {'workflow': value})
        else:
            result = self._call('POST', '/api/v2/workflow', {'name': name, 'workflow': value})
            workflow_id = identifier(result['workflow']['id'])
        stored = self._get(workflow_id)
        if stored['workflow'].get('agent_config', {}).get('llmConfig', {}).get('model') != self.client.AGENT_LLM['model'] or stored['workflow'].get('agent_config', {}).get('llmConfig', {}).get('provider') != self.client.AGENT_LLM['provider']:
            raise IntegrationError('Stored phone-agent model mismatch; publication blocked.')
        after = self._versions(workflow_id)
        new = [v for v in after if v['id'] not in {old['id'] for old in before}]
        if len(new) != 1 or new[0]['versionNumber'] != max(v['versionNumber'] for v in after):
            raise IntegrationError('New saved version is ambiguous; publication blocked.')
        v = new[0]
        return SavedWorkflow(workflow_id, stored['agentId'], v['id'], v['versionNumber'])

    def publish(self, saved, *, approved=False):
        if approved is not True:
            raise IntegrationError('Publication requires explicit approval.')
        stored = self._get(saved.workflow_id)
        if stored['agentId'] != saved.agent_id:
            raise IntegrationError('Agent identity mismatch.')
        self._prepared(stored['workflow'])  # Reject model conflicts and credentials.
        llm = stored['workflow'].get('agent_config', {}).get('llmConfig', {})
        if any(llm.get(k) != self.client.AGENT_LLM[k] for k in ('provider', 'model')):
            raise IntegrationError('Stored model is missing or incorrect; publication blocked.')
        versions = self._versions(saved.workflow_id)
        if not versions or max(versions, key=lambda v: v['versionNumber'])['id'] != saved.version_id:
            raise IntegrationError('Saved version is no longer newest; publication blocked.')
        selected = next(v for v in versions if v['id'] == saved.version_id)
        if selected['versionNumber'] != saved.version_number:
            raise IntegrationError('Version identity mismatch.')
        if not selected.get('isPublished'):
            self._call('POST', '/api/v2/versions/workflow/' + saved.workflow_id + '/publish', {'versionId': saved.version_id})

    def export(self, workflow_id, path):
        stored = self._get(workflow_id)
        value = deepcopy(stored['workflow'])
        value.pop('test_variables', None)
        self._no_credentials(value)
        private_write(path, {'workflow_id': stored['id'], 'agent_id': stored['agentId'], 'workflow': value})

    def trace(self, path):
        private_write(path, {'operations': self.events})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('local', 'dry-run', 'save', 'publish', 'export'))
    parser.add_argument('--workflow')
    parser.add_argument('--client', default='local_freya/starter_kit/freya.py')
    parser.add_argument('--allow-external', action='store_true')
    parser.add_argument('--approve-publish', action='store_true')
    parser.add_argument('--workflow-id')
    parser.add_argument('--agent-id')
    parser.add_argument('--version-id')
    parser.add_argument('--version-number', type=int)
    parser.add_argument('--output')
    parser.add_argument('--trace')
    args = parser.parse_args(argv)
    adapter = None
    try:
        workflow = load_workflow(args.workflow) if args.operation in ('local', 'dry-run', 'save') else None
        if args.operation == 'local':
            print('PASS: local structural validation only.')
            return 0
        if not args.allow_external:
            raise IntegrationError('External operations require --allow-external.')
        if args.operation == 'publish' and not args.approve_publish:
            raise IntegrationError('Publication requires --approve-publish after approval.')
        adapter = FreyaAdapter(official_client(args.client), allow_external=True, dry_run_only=args.operation == 'dry-run')
        if args.operation == 'dry-run':
            adapter.dry_run(workflow)
        elif args.operation == 'save':
            print(json.dumps(asdict(adapter.save(workflow, workflow_id=args.workflow_id))))
        elif args.operation == 'publish':
            adapter.publish(SavedWorkflow(identifier(args.workflow_id), identifier(args.agent_id), identifier(args.version_id), args.version_number), approved=True)
        else:
            if not args.output:
                raise IntegrationError('Export requires an output path.')
            adapter.export(args.workflow_id, args.output)
        print('Freya operation completed; no response content logged.')
        return 0
    except Exception:
        print('Operation failed; private exception content suppressed. Check configuration, local validation and version selection.')
        return 1
    finally:
        if adapter and args.trace:
            try:
                adapter.trace(args.trace)
            except Exception:
                print('Trace not saved; check outputs/ path and use a new filename.')


if __name__ == '__main__':
    raise SystemExit(main())
