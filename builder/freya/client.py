"""The Freya platform client: the starter kit's own modules, loaded from the kit folder.

The kit modules read FREYA_API_KEY, FREYA_API_URL and FREYA_BANK_TEAM_KEY when they are imported or called, so
keys must be in the environment first. Functions used of freya.py: validate_workflow (dry run), save_workflow,
export_workflow, and for a simulation create_personas, run_simulation, get_iteration, get_call.

Everything goes through the kit's `api(method, path, body, **params)`. The builder calls it directly in three
places, each on a route the kit has no function for: the versions of a workflow and publishing one of them
(freya/deployment.py), and the credit balance of the workspace (`credits` below). `patient` wraps it so that a
read is tried again when the platform or the network fails for a moment.
"""
import importlib.util
import time
from pathlib import Path

RETRIED = (502, 503, 504)


def load(kit, patiently=False):
    """The kit's freya.py as a module. With `patiently`, its reads are tried again on a passing failure."""
    spec = importlib.util.spec_from_file_location('freya', str(Path(kit) / 'freya.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if patiently:
        module.api = patient(module.api)
    return module


def load_runner(kit):
    """The kit's tools/run_cases.py as a module, without running it: the customer text of a practice case
    (`persona_for`), the bank mock's client (`http`) and the kit's scoring of a ledger (`score_ledger`).
    It imports the kit's freya.py itself, so `runner.freya` is the platform client a simulation uses."""
    spec = importlib.util.spec_from_file_location('run_cases', str(Path(kit) / 'tools' / 'run_cases.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def patient(api, tries=8, wait=6.0, sleep=time.sleep):
    """The kit's `api` with reads that are tried again when the platform answers 502, 503 or 504, or when the
    connection itself fails (a name that does not resolve, a reset).

    A simulation is polled for minutes; seen on 2026-10-11, one such answer in between ended the wait and the
    result of a simulation that was still running was lost, and a failed name lookup ended a step. Only reads
    are repeated: a request that creates or changes something is never sent twice."""
    def call(method, path, body=None, **params):
        for attempt in range(tries):
            try:
                return api(method, path, body, **params)
            except (RuntimeError, OSError) as exc:
                passing = isinstance(exc, OSError) or any('-> %d' % code in str(exc) for code in RETRIED)
                if method != 'GET' or attempt + 1 == tries or not passing:
                    raise
                sleep(wait)
    return call


def credits(freya):
    """What the platform says about the workspace's credits: `creditsLeft`, `creditUsdRate`. The platform states
    no price for a simulation; two readings around one show what it cost."""
    return freya.api('GET', '/api/v2/workspaces/%s/credits' % freya.workspace_id())
