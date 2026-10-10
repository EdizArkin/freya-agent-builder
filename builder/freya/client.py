"""The Freya platform client: the starter kit's own freya.py, loaded from the kit folder.

The builder adds no API calls of its own. The kit module reads FREYA_API_KEY and
FREYA_API_URL when it is imported, so keys must be in the environment first.
Functions used: validate_workflow (dry run), save_workflow, export_workflow.
"""
import importlib.util
from pathlib import Path


def load(kit):
    spec = importlib.util.spec_from_file_location('freya', str(Path(kit) / 'freya.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
