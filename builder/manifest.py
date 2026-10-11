"""The submission form of the event, written from files that exist.

    python -m builder manifest --team NAME --repo URL [--name hidden-1 --name hidden-2] [--out submission/form.json]

The form has six fields (https://www.freyavoice.ai/hackathon#submit, read on 2026-10-11): team, repo, commit,
run_command, and per hidden brief the path of its exported workflow and of its trace. This writes exactly those.
It refuses to name a file that is not there or a trace that is not the trace of a finished run. The commit it
names is the one the traces say they were written by (the builder that built the agents), unless one is given;
traces of two different commits make no form: a form is a statement about one commit.
"""
import argparse
import json
import re
from pathlib import Path

from builder.config import settings
from builder.freya import versioning

RUN_COMMAND = 'python3 builder/build.py {brief_path} {tool_api_path}'


def form(team, repo, names, root=settings.ROOT, commit=None):
    """The form as a dict, and the problems that keep it from being sent."""
    root, problems = Path(root), []
    built_by = set()
    for name in names:
        trace = root / ('traces/%s.log' % name)
        built_by.update(re.findall(r'builder commit ([0-9a-f]{40})', trace.read_text(encoding='utf-8', errors='replace')) if trace.is_file() else [])
    if not commit and len(built_by) > 1:
        problems.append('the traces were written by different commits: %s' % ', '.join(sorted(c[:12] for c in built_by)))
    out = {'team': team, 'repo': repo, 'commit': commit or (built_by.pop() if len(built_by) == 1 else versioning.builder_commit(root)), 'run_command': RUN_COMMAND, 'workflows': {}, 'traces': {}}
    if not out['commit']:
        problems.append('the commit of the repository cannot be read')
    for name in names:
        workflow, trace = 'submission/%s.workflow.json' % name, 'traces/%s.log' % name
        out['workflows'][name], out['traces'][name] = workflow, trace
        if not (root / workflow).is_file():
            problems.append('%s is not there' % workflow)
        else:
            try:
                if not isinstance(json.loads((root / workflow).read_text(encoding='utf-8')).get('workflow'), dict):
                    problems.append('%s is not an export of a workflow' % workflow)
            except ValueError:
                problems.append('%s is not JSON' % workflow)
        text = (root / trace).read_text(encoding='utf-8', errors='replace') if (root / trace).is_file() else None
        if text is None:
            problems.append('%s is not there' % trace)
        elif '| run end | status success |' not in text:
            problems.append('%s is not the trace of a run that ended in success' % trace)
        elif out['commit'] and ('builder commit %s' % out['commit']) not in text:
            problems.append('%s was not written by a run of commit %s' % (trace, out['commit'][:12]))
    return out, problems


def main(argv=None):
    p = argparse.ArgumentParser(prog='python -m builder manifest', description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--team', required=True)
    p.add_argument('--repo', required=True, help='the URL of the repository')
    p.add_argument('--name', action='append', default=[], help='a hidden brief by its file name without ending; default hidden-1 and hidden-2')
    p.add_argument('--commit', help='the commit to name; default: the one the traces were written by')
    p.add_argument('--out', help='write the form to this file; default: print it')
    args = p.parse_args(argv)
    made, problems = form(args.team, args.repo, args.name or ['hidden-1', 'hidden-2'], commit=args.commit)
    for problem in problems:
        print('NOT READY: ' + problem)
    if problems:
        return 1
    text = json.dumps(made, indent=1)
    if args.out:
        Path(args.out).write_text(text + '\n', encoding='utf-8')
    print(text)
    return 0
