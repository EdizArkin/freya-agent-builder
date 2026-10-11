"""The Freya platform client: the starter kit's own modules, loaded from the kit folder.

The builder adds no API calls of its own. The kit modules read FREYA_API_KEY, FREYA_API_URL and
FREYA_BANK_TEAM_KEY when they are imported or called, so keys must be in the environment first.
Functions used of freya.py: validate_workflow (dry run), save_workflow, export_workflow, and for a
simulation create_personas, run_simulation, get_iteration, get_call.
"""
import importlib.util
from pathlib import Path


def load(kit):
    spec = importlib.util.spec_from_file_location('freya', str(Path(kit) / 'freya.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_runner(kit):
    """The kit's tools/run_cases.py as a module, without running it: the customer text of a practice case
    (`persona_for`), the bank mock's client (`http`) and the kit's scoring of a ledger (`score_ledger`).
    It imports the kit's freya.py itself, so `runner.freya` is the platform client a simulation uses."""
    spec = importlib.util.spec_from_file_location('run_cases', str(Path(kit) / 'tools' / 'run_cases.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
