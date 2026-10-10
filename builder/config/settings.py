"""Where things are, which defaults apply, and how keys reach the process. Values of keys are never printed."""
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
KIT = ROOT / 'local_freya' / 'starter_kit'
OUTPUTS = ROOT / 'outputs'
DEFAULT_MODEL = 'claude-sonnet-5-5'
MODEL_VARIABLE = 'BUILDER_LLM_MODEL'
MAX_TOKENS = 32000


def env_files(extra, kit):
    """Files searched for keys, in order. The first file that sets a name wins; the process environment wins over all."""
    return list(extra) + [ROOT / '.env.sh', ROOT / '.env', Path(kit) / 'env.sh']


def load_env(paths):
    """Read KEY=VALUE / export KEY=VALUE files into the environment. Values are never printed."""
    for path in paths:
        if not Path(path).is_file():
            continue
        for line in Path(path).read_text(encoding='utf-8-sig').splitlines():
            line = line.strip()
            if line.startswith('export '):
                line = line[7:].strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, value = line.split('=', 1)
            key, value = key.strip(), value.strip()
            if value[:1] in ('"', "'"):
                # A quoted value ends at its closing quote; anything after it is a comment.
                end = value.find(value[0], 1)
                value = value[1:end] if end > 0 else value[1:]
            else:
                value = value.split(' #', 1)[0].strip()
            if key.isidentifier() and value and not os.environ.get(key):
                os.environ[key] = value


def extraction_model(requested=None):
    """--model, else BUILDER_LLM_MODEL, else the default."""
    return requested or os.environ.get(MODEL_VARIABLE) or DEFAULT_MODEL


def secret_values():
    """Values of environment variables that hold credentials, for redaction and leak checks."""
    return [v for k, v in os.environ.items() if v and len(v) >= 8 and re.search(r'(KEY|TOKEN|PASSWORD|SECRET)$', k, re.I)]
