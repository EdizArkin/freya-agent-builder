"""Where things are, which defaults apply, and how keys reach the process. Values of keys are never printed."""
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
KIT = ROOT / 'freya_starter_kit'
OUTPUTS = ROOT / 'outputs'
DEFAULT_MODEL = 'claude-sonnet-5-5'
MODEL_VARIABLE = 'BUILDER_LLM_MODEL'
MAX_TOKENS = 32000
# The module an optimization run loads for its evaluation, by name: the builder never imports it. What such a
# module has to offer is said in builder/optimization/loop.py.
EVALUATION = 'benchmarks.harness.evaluation'


def safe_console():
    """Make printing unable to end a run. A console that cannot show a character (a Windows code page and an
    arrow, a name, a quotation mark from a brief) raises on print; with this it prints a replacement instead."""
    import sys
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors='backslashreplace')
        except (AttributeError, ValueError, OSError):
            pass


def env_files(extra, kit):
    """Files searched for keys, in order. The first file that sets a name wins; the process environment wins over all."""
    return list(extra) + [ROOT / '.env.sh', ROOT / '.env', Path(kit) / 'env.sh']


def read_env(paths, names=None):
    """What KEY=VALUE / export KEY=VALUE files set, as {name: value}; the first file that sets a name wins. With
    `names`, only those. Nothing is put into the environment and no value is printed."""
    found = {}
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
            if key.isidentifier() and value and key not in found and (names is None or key in names):
                found[key] = value
    return found


def load_env(paths):
    """Read KEY=VALUE / export KEY=VALUE files into the environment. Values are never printed."""
    for key, value in read_env(paths).items():
        if not os.environ.get(key):
            os.environ[key] = value


def extraction_model(requested=None):
    """--model, else BUILDER_LLM_MODEL, else the default."""
    return requested or os.environ.get(MODEL_VARIABLE) or DEFAULT_MODEL


def configured_model(requested=None, kit=KIT):
    """The model a run would use, worked out without loading any key into this process: --model, else
    BUILDER_LLM_MODEL from the environment or the environment files, else the default."""
    return requested or os.environ.get(MODEL_VARIABLE) or read_env(env_files([], kit), (MODEL_VARIABLE,)).get(MODEL_VARIABLE) or DEFAULT_MODEL


def secret_values():
    """Values of environment variables that hold credentials, for redaction and leak checks."""
    return [v for k, v in os.environ.items() if v and len(v) >= 8 and re.search(r'(KEY|TOKEN|PASSWORD|SECRET)$', k, re.I)]
