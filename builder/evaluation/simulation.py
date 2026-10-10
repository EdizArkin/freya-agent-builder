"""Real phone-agent evaluation: the kit's practice customers, run on Freya against a deployed agent.

This starts simulations on the platform and reads the bank mock's ledger. It is
not the offline test suite and not the offline engine stand-in used by the tests;
a pass in one says nothing about the other.
"""
import subprocess
import sys


def run_practice_cases(kit, agent_id, brief_letter, results_path, trace):
    """Run the kit's tools/run_cases.py and copy its report into the trace. Returns its exit code."""
    cmd = [sys.executable, 'tools/run_cases.py', agent_id, '--brief', brief_letter, '--out', str(results_path)]
    trace('test: ' + ' '.join(cmd[1:]))
    done = subprocess.run(cmd, cwd=str(kit), capture_output=True, text=True, encoding='utf-8', errors='replace')
    for line in (done.stdout + done.stderr).splitlines():
        trace('test| ' + line)
    return done.returncode
