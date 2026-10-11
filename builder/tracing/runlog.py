"""The trace of one builder run as a plain-text .log file: what was done, in order, as it happened.

One line per event: time, run id, text. Every line is written and flushed to disk before the next thing
happens, so a run that is stopped or crashes leaves everything up to that point. Credentials are redacted: the
values of the environment's keys, and anything that looks like a bearer token or a key. The same line is
printed, so `python builder/build.py ... 2>&1 | tee traces/x.log` gives the same trace.

A RunLog is called like the build trace (`log('text')`), so every part of the builder that takes a `trace`
writes into it. It adds two things: each model call gets its number in the run, and nothing a run writes can
land in the log of another run (a path that exists is never opened again; the new log gets a number).
A log never decides anything: it is handed lines and writes them.
"""
import os
import re
import time
from pathlib import Path

from builder.config import settings

SECRETS = (
    (re.compile(r'(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}'), 'Bearer <redacted>'),
    (re.compile(r'\b(?:fak|sk|pk|key)[-_][A-Za-z0-9_-]{12,}'), '<redacted>'),
    # name=value, and "name": "value". Not `name: value` in running text: "the secret: nobody knows" is no credential.
    (re.compile(r'(?i)\b((?:api[_-]?key|authorization|token|secret|password)=["\']?)[^\s"\',}|]{6,}'), r'\1<redacted>'),
    (re.compile(r'(?i)(["\'][\w-]*(?:api[_-]?key|authorization|token|secret|password)["\']\s*:\s*["\']?)[^\s"\',}|]{6,}'), r'\1<redacted>'),
)


def redact(text):
    text = str(text)
    for value in settings.secret_values():
        text = text.replace(value, '<redacted>')
    for pattern, put in SECRETS:
        text = pattern.sub(put, text)
    return text


def say(row):
    """Print one line. A console that cannot show it gets it with the characters it lacks replaced: the line is
    already in the file, and the print must not be what ends a run."""
    try:
        print(row, flush=True)
    except UnicodeEncodeError:
        print(row.encode('ascii', 'backslashreplace').decode('ascii'), flush=True)
    except (OSError, ValueError):
        pass


class RunLog:
    def __init__(self, path, run_id, echo=say):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        number = 1
        while True:
            self.path = path if number == 1 else path.with_name('%s-%d%s' % (path.stem, number, path.suffix))
            try:
                self.file = self.path.open('x', encoding='utf-8')       # never the log of another run
                break
            except FileExistsError:
                number += 1
        self.run_id, self.echo, self.model_calls, self.started = str(run_id), echo, 0, time.time()

    def __call__(self, line):
        text = redact(line)
        if text.startswith('model call: '):
            self.model_calls += 1
            text = 'LLM call %d | %s' % (self.model_calls, text[len('model call: '):])
        for part in text.splitlines() or ['']:
            row = '%s | %s | %s' % (time.strftime('%Y-%m-%dT%H:%M:%S%z'), self.run_id, part)
            if not self.file.closed:
                self.file.write(row + '\n')
                self.file.flush()
                try:
                    os.fsync(self.file.fileno())
                except OSError:
                    pass
            if self.echo:
                self.echo(row)

    def close(self):
        if not self.file.closed:
            self.file.close()
