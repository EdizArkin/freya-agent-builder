"""The trace of one build: plain text, one timestamped line per event, credentials redacted."""
import time
from pathlib import Path

from builder.config import settings


class Trace:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text('', encoding='utf-8')

    def __call__(self, line):
        for value in settings.secret_values():
            line = line.replace(value, '<redacted>')
        stamp = time.strftime('%Y-%m-%dT%H:%M:%S%z')
        with self.path.open('a', encoding='utf-8') as out:
            out.write(stamp + ' ' + line + '\n')
        print(line, flush=True)
