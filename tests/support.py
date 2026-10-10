"""Inputs shared by the tests: the starter kit's tool API file and practice cases."""
import json
from pathlib import Path

KIT = Path('local_freya/starter_kit')
OPENAPI = json.loads((KIT / 'tools/openapi.json').read_text(encoding='utf-8'))
CASES = {c['id']: c for c in json.loads((KIT / 'public-test-set.json').read_text(encoding='utf-8'))}
