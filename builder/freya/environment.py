"""Parse a deliberately small data-only subset of project-root .env.sh."""
import os
from pathlib import Path
import re


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ALLOWED_KEYS = frozenset({
    'FREYA_API_KEY', 'FREYA_API_URL', 'FREYA_WORKSPACE_ID',
    'FREYA_AGENT_PROVIDER', 'FREYA_AGENT_MODEL', 'FREYA_SIM_CONCURRENCY',
})


class EnvironmentConfigError(ValueError):
    """Fixed messages intentionally exclude paths, variable names and values."""

    def __init__(self, reason, line=None):
        self.reason = reason
        self.line = line
        super().__init__(f'Environment configuration error: {reason}; line={line}.')


def load_project_environment(path=None, *, environ=None):
    """Validate the entire file before filling absent/empty process values.

    Optional path/environment arguments support offline tests. Never invoke a shell,
    expand variables, interpret escapes, log values or return loaded values.
    """
    path = PROJECT_ROOT / '.env.sh' if path is None else Path(path)
    target = os.environ if environ is None else environ
    try:
        text = path.read_text(encoding='utf-8-sig')
    except FileNotFoundError:
        return
    except (OSError, UnicodeError):
        raise EnvironmentConfigError('file_read') from None
    values = {}
    for number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        match = re.fullmatch(r'export[ \t]+([A-Z][A-Z0-9_]*)=(.*)', line)
        if not match:
            raise EnvironmentConfigError('assignment_syntax', number)
        if match[1] not in ALLOWED_KEYS:
            # A shared shell config can include unrelated exports. Never load them.
            continue
        if match[1] in values:
            raise EnvironmentConfigError('duplicate_assignment', number)
        raw = match[2].strip()
        if raw.startswith(('"', "'")):
            quoted = re.fullmatch(r'([\'\"])(.*?)\1[ \t]*(?:#.*)?', raw)
            if not quoted:
                raise EnvironmentConfigError('quoted_syntax', number)
            value = quoted[2]
        else:
            # A trailing comment must be separated by whitespace. No shell tokens.
            value = re.split(r'[ \t]+#', raw, maxsplit=1)[0].rstrip()
            if re.search(r'[\s\'";|&<>\\()]', value):
                raise EnvironmentConfigError('value_syntax', number)
        if any(c in value for c in ('$','`','\x00')) or any(ord(c) < 32 for c in value):
            raise EnvironmentConfigError('expansion_or_control', number)
        values[match[1]] = value
    for key, value in values.items():
        if not target.get(key):
            target[key] = value
