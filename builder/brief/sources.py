"""Ordered, lossless UTF-8 brief ingestion; no policy interpretation."""
from dataclasses import dataclass
import hashlib
from pathlib import Path
import re

from builder.policy.models import SourceDocument, SourceRef


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class SourceSection:
    id: str
    reference: SourceRef
    text: str


@dataclass(frozen=True)
class BriefBundle:
    documents: tuple[SourceDocument, ...]
    sections: tuple[SourceSection, ...]
    originals: tuple[tuple[str, bytes], ...]

    def source_bytes(self):
        return dict(self.originals)


class SourceError(ValueError):
    """Fixed messages only; never echo paths or document contents."""


def load_briefs(paths) -> BriefBundle:
    documents, sections, originals, seen = [], [], [], set()
    for order, path in enumerate(paths):
        try:
            raw = Path(path).read_bytes()
            text = raw.decode('utf-8')
        except (OSError, UnicodeError, TypeError):
            raise SourceError('Cannot read a brief as original UTF-8 bytes.') from None
        if not text.strip():
            raise SourceError('Brief documents must contain nonempty text.')
        sha = digest(raw)
        if sha in seen:
            raise SourceError('Duplicate document content; supply each source once.')
        seen.add(sha)
        ident = 'doc-' + sha
        documents.append(SourceDocument(ident, sha, order, 'brief'))
        originals.append((ident, raw))
        # Markdown headings partition source, including preamble and nested headings.
        # Plain text is one section. This is segmentation, not a normative classifier.
        headings = list(re.finditer(r'(?m)^#{1,6}[ \t]+[^\r\n]+', text))
        starts = sorted(set([0] + [h.start() for h in headings] + [len(text)]))
        labels = {h.start(): h.group().lstrip('#').strip() for h in headings}
        for start, end in zip(starts, starts[1:]):
            span = text[start:end]
            if not span.strip():
                continue
            ref = SourceRef(ident, labels.get(start, 'document preamble'), start, end, digest(span.encode('utf-8')))
            sections.append(SourceSection(f'{ident}:{start}:{end}', ref, span))
    if not documents:
        raise SourceError('At least one brief path is required.')
    return BriefBundle(tuple(documents), tuple(sections), tuple(originals))
