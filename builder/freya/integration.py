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
from urllib.error import HTTPError, URLError


HINTS = {
    'artifact_path': 'Choose a new file below the repository outputs/ directory; private artifacts cannot be written elsewhere.',
    'artifact_exists': 'Use a new outputs/ filename; existing artifacts are never overwritten.',
    'artifact_write': 'Check local output-directory permissions and available storage.',
    'artifact_credentials': 'Export blocked by credential screening; review platform configuration privately without printing or exporting secrets.',
    'workflow_response': 'Stored workflow response has unexpected shape or identity; check official client compatibility privately.',
    'workspace_missing': 'Set FREYA_WORKSPACE_ID to the explicitly authorized workspace ID before save, publish or export.',
    'workspace_invalid': 'Check FREYA_WORKSPACE_ID format locally; use the authorized workspace identifier without surrounding whitespace.',
    'operation_permission': 'Use explicit external opt-in and write mode for saving; dry-run-only mode cannot save.',
    'stored_model_mismatch': 'Stored phone-agent configuration differs from the mandated model; inspect the saved workflow privately before any publication.',
    'environment_parsing': 'Check the indicated .env.sh line for simple export syntax; no shell expansion is supported.',
    'environment_file': 'Check project-root .env.sh read permissions and UTF-8 encoding.',
    'client_missing': 'Restore the trusted official freya.py file or select its correct local path.',
    'client_initialization': 'Check trusted client compatibility and configuration types, including FREYA_SIM_CONCURRENCY.',
    'workflow_missing': 'Select an existing readable local workflow JSON file.',
    'workflow_configuration': 'Check local workflow JSON syntax, graph, tools and phone-agent configuration.',
    'configuration': 'Check process environment, trusted client path, local workflow and explicit operation permissions.',
    'authentication': 'Check key validity, workspace permissions and access restrictions; do not print the key.',
    'endpoint_missing': 'Check FREYA_API_URL against the official base URL and toolkit route.',
    'validation': 'Review workflow schema, null fields and agent configuration against the toolkit locally.',
    'rate_limit': 'Wait before an explicitly authorized retry; avoid parallel requests.',
    'server': 'Freya or its gateway failed; retry the dry-run later if authorized.',
    'network': 'Check DNS, proxy, TLS trust and connectivity to the authorized Freya host.',
    'timeout': 'The request timed out; check connectivity and retry the dry-run later if authorized.',
    'http': 'Check the official API contract for this HTTP status.',
    'response': 'The response was rejected or unreadable; check endpoint and toolkit compatibility.',
    'unknown': 'Failure could not be classified safely; inspect the adapter configuration locally.',
}


def safe_endpoint(path):
    # Use route templates, never response URLs, query strings or supplied identifiers.
    if path == '/api/v2/workflow':
        return path
    if re.fullmatch(r'/api/v2/workflow/[^/?]+', path):
        return '/api/v2/workflow/{workflowId}'
    if re.fullmatch(r'/api/v2/versions/workflow/[^/?]+(?:/publish)?', path):
        return '/api/v2/versions/workflow/{workflowId}' + ('/publish' if path.endswith('/publish') else '')
    return None


def safe_validation_details(text):
    """Untrusted JSON only: no arbitrary messages, values or field names escape."""
    if len(text) > 16384:
        return []
    try:
        value = json.loads(text)
    except (ValueError, RecursionError):
        return []
    codes = {'invalid_type', 'invalid_value', 'invalid_enum_value', 'unrecognized_keys',
             'too_small', 'too_big', 'invalid_string', 'required', 'missing', 'type_error'}
    fields = {'workflow', 'name', 'nodes', 'global_nodes', 'initial_node', 'tools',
              'function', 'parameters', 'properties', 'required', 'config', 'node_type',
              'max_turn_transition', 'passthrough_default_transition', 'agent_config',
              'llmConfig', 'provider', 'model', 'transitions', 'target_node', 'condition',
              'on_enter', 'type', 'handler', 'default', 'body', 'headers', 'url', 'method'}
    details = []
    def visit(item, depth=0):
        if depth > 5 or len(details) >= 20:
            return
        if isinstance(item, dict):
            path, code = item.get('path'), item.get('code')
            if isinstance(path, list) and len(path) <= 20 and isinstance(code, str) and code in codes:
                clean = [p if isinstance(p, str) and p in fields else '*' for p in path]
                if clean:
                    details.append({'path': clean, 'code': code})
            for key in ('data', 'error', 'errors', 'issues', 'details'):
                if key in item:
                    visit(item[key], depth + 1)
        elif isinstance(item, list):
            for child in item[:40]:
                visit(child, depth + 1)
    visit(value)
    return details


def export_credential_findings(value):
    """Diagnostic paths only; unknown property names and all indices are masked."""
    allowed = {'agent_config', 'llmConfig', 'ttsConfig', 'sttConfig', 'apiKey', 'api_key',
               'accessToken', 'access_token', 'password', 'secret', 'token', 'Authorization',
               'authorization', 'provider', 'model', 'nodes', 'global_nodes', 'tools', 'config',
               'function', 'parameters', 'properties', 'headers', 'url', 'default', 'body'}
    sensitive = {'authorization', 'apikey', 'accesstoken', 'password', 'secret', 'token'}
    secrets = [s for k, s in os.environ.items() if s and re.search(r'(KEY|TOKEN|PASSWORD|SECRET)$', k, re.I)]
    findings = []
    def add(path, classification):
        if len(findings) < 20:
            findings.append({'path': path, 'classification': classification})
    def visit(item, path, depth=0):
        if depth > 50 or len(findings) >= 20:
            return
        if isinstance(item, dict):
            for key, child in item.items():
                child_path = path + [key if key in allowed else '*']
                if re.sub(r'[^a-z]', '', key.lower()) in sensitive:
                    add(child_path, 'credential_field_empty' if child is None or child == '' else 'credential_field_potentially_sensitive')
                # Match secrets against key text too, without exposing the key.
                if any(s in key for s in secrets):
                    add(path + ['*'], 'known_process_secret')
                visit(child, child_path, depth + 1)
        elif isinstance(item, list):
            for child in item:
                visit(child, path + ['*'], depth + 1)
        elif isinstance(item, str):
            if any(s in item for s in secrets):
                add(path, 'known_process_secret')
            elif re.search(r'(?i)fak_[a-z0-9_-]+|bearer\s+[a-z0-9]', item):
                add(path, 'credential_like_text')
            if '://' in item:
                try:
                    parsed = urlparse(item)
                    if parsed.username or parsed.password:
                        add(path, 'url_credentials')
                except ValueError:
                    pass
    visit(value, ['workflow'])
    return findings


def review_copy(workflow):
    value = deepcopy(workflow)
    config = value.get('agent_config')
    if isinstance(config, dict):
        for name in ('llmConfig', 'sttConfig', 'ttsConfig'):
            settings = config.get(name)
            if isinstance(settings, dict) and 'apiKey' in settings and (
                    settings['apiKey'] is None or settings['apiKey'] == ''):
                del settings['apiKey']
    return value


def structural_diff(local, remote, *, limit=100):
    """Bounded path-only diagnostics. Unknown keys are masked, never ignored."""
    fields = {'initial_node', 'nodes', 'global_nodes', 'tools', 'agent_config', 'llmConfig',
              'sttConfig', 'ttsConfig', 'apiKey', 'name', 'node_type', 'transitions',
              'target_node', 'condition', 'operator', 'variable', 'operand', 'logic',
              'conditions', 'on_enter', 'text', 'type', 'task_message', 'role_message',
              'system_prompt', 'test_variables', 'function', 'handler', 'parameters',
              'properties', 'required', 'default', 'config', 'response_mapping',
              'extraction_schema', 'pre_actions', 'terminal', 'max_turn_transition',
              'passthrough_default_transition', 'max_node_turns', 'passthrough',
              'assertions', 'position', 'interruptible', 'timeoutMs', 'maxRetries',
              'provider', 'model', 'truncation_strategy', 'truncation_window_size',
              'suppress_tool_results', 'disable_inactivity_monitor',
              'on_enter_first_entry_only', 'return_to_previous_on_interrupt'}
    changes, truncated = [], False
    def add(path, category, classification='material_or_unconfirmed'):
        nonlocal truncated
        if len(changes) >= limit:
            truncated = True
            return
        changes.append({'path': path, 'category': category, 'classification': classification})
    def visit(a, b, path, depth=0):
        nonlocal truncated
        if depth > 50 or len(changes) >= limit:
            truncated = True
            return
        if type(a) is not type(b):
            add(path, 'type_changed')
        elif isinstance(a, dict):
            for key in sorted(a.keys() | b.keys()):
                p = path + [key if key in fields else '*']
                if key not in a:
                    known = path == ['workflow'] and key == 'agent_config'
                    empty_metadata = (len(path) == 3 and path[:2] in (
                        ['workflow', 'nodes'], ['workflow', 'global_nodes']) and
                        key in ('assertions', 'response_mapping', 'extraction_schema') and b[key] == [])
                    classification = ('known_platform_default' if known else
                                      'empty_optional_metadata' if empty_metadata else 'material_or_unconfirmed')
                    add(p, 'added', classification)
                elif key not in b:
                    add(p, 'removed')
                else:
                    visit(a[key], b[key], p, depth + 1)
        elif isinstance(a, list):
            for i in range(max(len(a), len(b))):
                p = path + [i]
                if i >= len(a): add(p, 'added')
                elif i >= len(b): add(p, 'removed')
                else: visit(a[i], b[i], p, depth + 1)
        elif a != b:
            add(path, 'value_changed')
    visit(review_copy(local), review_copy(remote), ['workflow'])
    return {'changes': changes, 'truncated': truncated}

from builder.compiler.validate import parse_workflow, validate_workflow
from builder.freya.environment import EnvironmentConfigError, load_project_environment


class IntegrationError(ValueError):
    """Messages never include API responses, credentials or workflow content."""

    def __init__(self, message='', *, category='configuration', http_status=None, endpoint=None, reason=None, line=None):
        self.diagnostic = {'category': category, 'http_status': http_status,
                           'endpoint': endpoint, 'hint': HINTS[category]}
        if reason is not None:
            self.diagnostic['reason'] = reason
        if line is not None:
            self.diagnostic['line'] = line
        super().__init__(json.dumps(self.diagnostic, sort_keys=True))


def classify_failure(exc, method, path):
    status = None
    details = []
    if isinstance(exc, HTTPError):
        status = exc.code
    elif type(exc) is RuntimeError:
        # Official api() discards HTTPError and embeds status in a fixed prefix.
        # Read only that prefix; never parse body text or use it as a diagnostic.
        match = re.match(r'^' + re.escape(method + ' ' + path) + r' -> ([1-5][0-9]{2}):', str(exc))
        if match:
            status = int(match[1])
            if status in (400, 422):
                details = safe_validation_details(str(exc)[match.end():].strip())
    if status is not None:
        category = ('authentication' if status in (401, 403) else
                    'endpoint_missing' if status == 404 else
                    'validation' if status in (400, 422) else
                    'rate_limit' if status == 429 else
                    'server' if 500 <= status <= 599 else 'http')
    elif isinstance(exc, TimeoutError) or (isinstance(exc, URLError) and isinstance(exc.reason, TimeoutError)):
        category = 'timeout'
    elif isinstance(exc, (URLError, ConnectionError, OSError)):
        category = 'network'
    elif isinstance(exc, ValueError):
        category = 'response'
    else:
        category = 'unknown'
    error = IntegrationError(category=category, http_status=status, endpoint=safe_endpoint(path))
    if details:
        error.diagnostic['validation_details'] = details
        error.args = (json.dumps(error.diagnostic, sort_keys=True),)
    return error


def load_workflow(path):
    try:
        workflow = parse_workflow(Path(path).read_text(encoding='utf-8'))
        if not validate_workflow(workflow).valid:
            raise ValueError()
        return workflow
    except OSError:
        raise IntegrationError(category='workflow_missing') from None
    except (UnicodeError, ValueError, RecursionError, TypeError):
        raise IntegrationError(category='workflow_configuration') from None


def official_client(path):
    """Import only when an external operation was explicitly requested."""
    try:
        load_project_environment()
    except EnvironmentConfigError as exc:
        raise IntegrationError(category='environment_file' if exc.reason == 'file_read' else 'environment_parsing',
                               reason=exc.reason, line=exc.line) from None
    if not os.environ.get('FREYA_API_KEY', '').strip():
        raise IntegrationError('FREYA_API_KEY is required.')
    try:
        url = urlparse(os.environ.get('FREYA_API_URL', 'https://staging-app.freyavoice.ai'))
        valid_url = (url.scheme == 'https' and url.hostname and not url.username and not url.password
                     and url.path in ('', '/') and not url.query and not url.fragment and url.port != 0)
    except ValueError:
        valid_url = False
    if not valid_url:
        raise IntegrationError('FREYA_API_URL must be an HTTPS origin without credentials.')
    if not Path(path).is_file():
        raise IntegrationError(category='client_missing')
    try:
        spec = importlib.util.spec_from_file_location('_official_freya', Path(path))
        client = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(client)
        return client
    except Exception:
        raise IntegrationError(category='client_initialization') from None


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
        raise IntegrationError(category='artifact_path')
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('x', encoding='utf-8') as stream:
            json.dump(data, stream, sort_keys=True, indent=2, allow_nan=False)
    except FileExistsError:
        raise IntegrationError(category='artifact_exists') from None
    except OSError:
        raise IntegrationError(category='artifact_write') from None


class FreyaAdapter:
    def __init__(self, client, *, allow_external=False, dry_run_only=True, read_only=False):
        self.client = client
        self.allow_external = allow_external
        self.dry_run_only = dry_run_only
        self.read_only = read_only
        self.events = []

    def _call(self, method, path, body=None, **params):
        if not self.allow_external:
            raise IntegrationError('External operations require explicit opt-in.')
        if self.read_only and method != 'GET':
            raise IntegrationError(category='operation_permission')
        if self.dry_run_only and not (method == 'POST' and path == '/api/v2/workflow'
                                     and params == {'dryRun': 'true'}):
            raise IntegrationError('Dry-run-only mode forbids this operation.')
        try:
            result = self.client.api(method, path, body, **params)
            if isinstance(result, dict) and result.get('success') is False:
                raise ValueError()
        except Exception as exc:
            error = classify_failure(exc, method, path)
            self.events.append({'operation': method, 'status': 'failed', 'diagnostic': error.diagnostic})
            raise error from None
        self.events.append({'operation': method, 'status': 'completed'})
        return result

    def _workspace(self):
        # Require explicit workspace selection rather than the client's first-workspace fallback.
        value = os.environ.get('FREYA_WORKSPACE_ID')
        if not value or not value.strip():
            raise IntegrationError(category='workspace_missing')
        try:
            return identifier(value)
        except IntegrationError:
            raise IntegrationError(category='workspace_invalid') from None

    def _prepared(self, workflow):
        if not validate_workflow(workflow).valid:
            raise IntegrationError(category='workflow_configuration')
        value = deepcopy(workflow)
        model = self.client.AGENT_LLM
        if 'agent_config' in value:
            config = value['agent_config']
            if not isinstance(config, dict) or not isinstance(config.get('llmConfig', {}), dict):
                raise IntegrationError(category='workflow_configuration')
            llm = config.get('llmConfig', {})
            if any(k in llm and llm[k] != model[k] for k in ('provider', 'model')):
                raise IntegrationError(category='workflow_configuration')
        self._no_credentials(value)
        return value

    @staticmethod
    def _no_credentials(value):
        text = json.dumps(value, allow_nan=False)
        secrets = [v for k, v in os.environ.items()
                   if re.search(r'(KEY|TOKEN|PASSWORD|SECRET)$', k, re.I)]
        if any(s and s in text for s in secrets) or re.search(r'(?i)fak_[a-z0-9_-]+|bearer\s+[a-z0-9]', text):
            raise IntegrationError(category='artifact_credentials')
        def check(item):
            if isinstance(item, dict):
                for key, child in item.items():
                    if re.sub(r'[^a-z]', '', key.lower()) in (
                            'authorization', 'apikey', 'accesstoken', 'password', 'secret', 'token'):
                        raise IntegrationError(category='artifact_credentials')
                    check(child)
            elif isinstance(item, list):
                for child in item:
                    check(child)
            elif isinstance(item, str) and '://' in item:
                parsed = urlparse(item)
                if parsed.username or parsed.password:
                    raise IntegrationError(category='artifact_credentials')
        check(value)

    def dry_run(self, workflow, name='builder validation', *, omit_injected_agent_config=False):
        value = self._prepared(workflow)
        # Legacy comparison option is now a no-op; retain its dry-run restriction.
        if omit_injected_agent_config and not self.dry_run_only:
            raise IntegrationError('Comparison requires dry-run-only mode.')
        self._call('POST', '/api/v2/workflow', {'name': name, 'workflow': value}, dryRun='true')
        # The dry-run payload is undocumented; never print or persist it.

    def _get(self, workflow_id):
        result = self._call('GET', '/api/v2/workflow/' + identifier(workflow_id), workspaceId=self._workspace())
        value = result.get('workflow', result) if isinstance(result, dict) else None
        if not isinstance(value, dict) or value.get('id') != workflow_id or not isinstance(value.get('workflow'), dict):
            raise IntegrationError(category='workflow_response')
        try:
            identifier(value.get('agentId'))
        except IntegrationError:
            raise IntegrationError(category='workflow_response') from None
        return value

    def _versions(self, workflow_id):
        result = self._call('GET', '/api/v2/versions/workflow/' + identifier(workflow_id), workspaceId=self._workspace())
        versions = result.get('versions', result) if isinstance(result, dict) else result
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
            raise IntegrationError(category='operation_permission')
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
            raise IntegrationError(category='stored_model_mismatch')
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
            raise IntegrationError(category='stored_model_mismatch')
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
        value = review_copy(stored['workflow'])
        value.pop('test_variables', None)
        # Confirmed platform placeholders: review-artifact copy only. All other
        # credential fields, including nested/unknown paths, retain strict screening.
        try:
            self._no_credentials(value)
        except IntegrationError as exc:
            if exc.diagnostic['category'] == 'artifact_credentials':
                findings = export_credential_findings(value)
                exc.diagnostic['credential_findings'] = findings or [
                    {'path': ['workflow'], 'classification': 'credential_screen_match_unlocalized'}]
                exc.args = (json.dumps(exc.diagnostic, sort_keys=True),)
            raise exc from None
        private_write(path, {'workflow_id': stored['id'], 'agent_id': stored['agentId'], 'workflow': value})

    def verify(self, saved, workflow):
        checks = dict.fromkeys(('workflow_identity', 'agent_identity', 'version_identity',
                               'version_number', 'newest_version', 'mandated_model',
                               'workflow_consistency', 'credential_screen'), False)
        diagnostics, state = [], 'unknown'
        differences = {'changes': [], 'truncated': False}
        try:
            stored = self._get(saved.workflow_id)
            checks['workflow_identity'] = stored['id'] == saved.workflow_id
            checks['agent_identity'] = stored['agentId'] == saved.agent_id
            remote = review_copy(stored['workflow'])
            config = remote.get('agent_config', {})
            llm = config.get('llmConfig', {}) if isinstance(config, dict) else {}
            checks['mandated_model'] = isinstance(llm, dict) and all(
                llm.get(k) == self.client.AGENT_LLM[k] for k in ('provider', 'model'))
            try:
                self._no_credentials(remote)
                checks['credential_screen'] = True
            except IntegrationError as exc:
                diagnostics.append(exc.diagnostic)
            expected = review_copy(workflow)
            differences = structural_diff(workflow, stored['workflow'])
            if 'agent_config' not in expected:
                remote.pop('agent_config', None)
            checks['workflow_consistency'] = remote == expected
        except IntegrationError as exc:
            diagnostics.append(exc.diagnostic)
        try:
            versions = self._versions(saved.workflow_id)
            selected = [v for v in versions if v['id'] == saved.version_id]
            checks['version_identity'] = len(selected) == 1
            checks['version_number'] = len(selected) == 1 and selected[0]['versionNumber'] == saved.version_number
            checks['newest_version'] = bool(versions) and len(selected) == 1 and selected[0]['versionNumber'] == max(v['versionNumber'] for v in versions)
            if selected:
                flag = selected[0].get('isPublished')
                state = 'published' if flag is True else 'unpublished' if flag is False else 'unknown'
        except IntegrationError as exc:
            diagnostics.append(exc.diagnostic)
        return {'checks': {k: 'PASS' if v else 'FAIL' for k, v in checks.items()},
                'publication_state': state, 'diagnostics': diagnostics,
                'verified': all(checks.values()), 'workflow_diff': differences}

    def trace(self, path):
        private_write(path, {'operations': self.events})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('local', 'dry-run', 'save', 'publish', 'export', 'verify'))
    parser.add_argument('--workflow')
    parser.add_argument('--client', default='local_freya/starter_kit/freya.py')
    parser.add_argument('--allow-external', action='store_true')
    parser.add_argument('--approve-publish', action='store_true')
    parser.add_argument('--omit-injected-agent-config', action='store_true',
                        help='Legacy dry-run-only no-op; configuration is now always preserved')
    parser.add_argument('--workflow-id')
    parser.add_argument('--agent-id')
    parser.add_argument('--version-id')
    parser.add_argument('--version-number', type=int)
    parser.add_argument('--output')
    parser.add_argument('--trace')
    args = parser.parse_args(argv)
    adapter = None
    try:
        if args.omit_injected_agent_config and args.operation != 'dry-run':
            raise IntegrationError('Comparison option is restricted to dry-run.')
        workflow = load_workflow(args.workflow) if args.operation in ('local', 'dry-run', 'save', 'verify') else None
        if args.operation == 'local':
            print('PASS: local structural validation only.')
            return 0
        if not args.allow_external:
            raise IntegrationError('External operations require --allow-external.')
        if args.operation == 'publish' and not args.approve_publish:
            raise IntegrationError('Publication requires --approve-publish after approval.')
        saved = None
        if args.operation == 'verify':
            saved = SavedWorkflow(identifier(args.workflow_id), identifier(args.agent_id), identifier(args.version_id), args.version_number)
            if type(saved.version_number) is not int or saved.version_number < 1:
                raise IntegrationError(category='configuration')
        adapter = FreyaAdapter(official_client(args.client), allow_external=True, dry_run_only=args.operation == 'dry-run', read_only=args.operation == 'verify')
        if args.operation == 'verify':
            result = adapter.verify(saved, workflow)
            print(json.dumps(result, sort_keys=True))
            return 0 if result['verified'] else 1
        if args.operation == 'dry-run':
            adapter.dry_run(workflow, omit_injected_agent_config=args.omit_injected_agent_config)
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
    except Exception as exc:
        error = exc if isinstance(exc, IntegrationError) else IntegrationError(category='unknown')
        print(json.dumps(error.diagnostic, sort_keys=True))
        return 1
    finally:
        if adapter and args.trace:
            try:
                adapter.trace(args.trace)
            except Exception:
                print('Trace not saved; check outputs/ path and use a new filename.')


if __name__ == '__main__':
    raise SystemExit(main())
