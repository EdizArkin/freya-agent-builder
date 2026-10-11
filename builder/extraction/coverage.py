"""Does a spec carry every mandatory rule of the documents it was extracted from?

A brief without test customers has nothing that would show a rule the extraction dropped. This audit is the
one check of that: one model call lists the mandatory requirements of the documents and says, for each, where
the spec carries it; code then checks every quotation the model gave. The model reads, code decides what its
reading is worth:

    COVERED     the sentence is in the documents word for word, the passage named is in the spec word for
                word, and every figure of the sentence occurs in the spec.
    OVERRIDDEN  the sentence is in the documents, and the sentence said to replace it stands later: in a later
                document, or further down in the same one. An earlier rule that no later text replaces stays
                a requirement.
    MISSING     the sentence is in the documents and the model finds nothing in the spec that carries it.
    AMBIGUOUS   the model calls the coverage partial or unclear, or it calls it covered while a figure of the
                sentence occurs nowhere in the spec.
    UNVERIFIED  a quotation could not be confirmed, so the entry says nothing either way.

What this does not prove. The list of requirements is the model's: a rule it did not list is not seen (the
number of lines of the documents that no entry quotes is reported for that reason). COVERED means that the
quotations are real, not that the passage means what the sentence means. A figure occurring in the spec is
never taken as coverage; its absence only makes a reported omission firm.

An omission is firm when it is reported as MISSING, with a source sentence that is in the documents, and with
a figure or code word of that sentence that occurs nowhere in the spec. Before that, one revision of the spec
is tried when the run can afford the call. A run reports a firm omission and publishes; asked to
(--coverage block), it stops before anything is published. Nothing else ever stops a run here.
"""
import json
import re

from builder.extraction import claude
from builder.spec.models import TOOLS

COVERED, MISSING, OVERRIDDEN, AMBIGUOUS, UNVERIFIED = 'COVERED', 'MISSING', 'OVERRIDDEN', 'AMBIGUOUS', 'UNVERIFIED'
STATUSES = (COVERED, OVERRIDDEN, MISSING, AMBIGUOUS, UNVERIFIED)
MIN_QUOTE = 12              # characters of a quotation, after whitespace is squeezed; the same as for an audit finding

PROMPT = """You check whether a specification for a phone agent carries every mandatory requirement of the bank documents it was written from. You are given the documents, in order, and the specification as lines of the form `path: value`.

A later document amends an earlier one. A requirement of an earlier document still applies unless a later document replaces that requirement explicitly; a later document that is silent about it changes nothing.

List every mandatory requirement of the documents: every rule, exception, prohibition, verification requirement, numerical limit, deadline, restriction on a tool, condition of a transfer and prescribed outcome of a call that the agent has to follow. One entry per requirement. Do not merge two requirements into one entry and do not list background text.

Reply with one JSON array and nothing else. One object per requirement:
{"requirement": "<the requirement in one sentence>",
 "kind": "rule" | "exception" | "verification" | "limit" | "deadline" | "tool" | "transfer" | "outcome",
 "source": "<the sentence or clause of the document that states it, copied word for word>",
 "status": "COVERED" | "MISSING" | "OVERRIDDEN" | "AMBIGUOUS",
 "spec": "<for COVERED and AMBIGUOUS: the passage of the specification that carries it, copied word for word from one line; for a structured field the whole line `path: value`>",
 "replaced_by": "<for OVERRIDDEN: the sentence of the later document that replaces it, copied word for word>",
 "note": "<for MISSING and AMBIGUOUS: what is absent or unclear, in one sentence>"}

COVERED: the specification states the requirement completely, in a structured field or in its rule text. MISSING: nothing in the specification carries it. OVERRIDDEN: a later document explicitly replaces it; list the rule that replaces it as a requirement of its own. AMBIGUOUS: the specification carries part of it, or carries it in a way that may differ from the document.

Code checks every quotation against the text. An entry whose quotation is not in the text word for word counts for nothing, so copy and never paraphrase. Similar words are not coverage: mark a requirement COVERED only if you can quote the passage that states it."""

_WORDS = {'zero': '0', 'one': '1', 'two': '2', 'three': '3', 'four': '4', 'five': '5', 'six': '6', 'seven': '7', 'eight': '8', 'nine': '9', 'ten': '10', 'eleven': '11',
          'twelve': '12', 'thirteen': '13', 'fourteen': '14', 'fifteen': '15', 'twenty': '20', 'thirty': '30', 'forty': '40', 'fifty': '50', 'sixty': '60', 'ninety': '90',
          'hundred': '100', 'once': '1', 'twice': '2', 'first': '1', 'second': '2', 'third': '3', 'fourth': '4', 'fifth': '5', 'single': '1', 'both': '2'}


def view(spec):
    """The spec as lines `path: value`, one per value: the text the audit is given and quotes from."""
    lines = []

    def walk(value, path):
        if isinstance(value, dict) and value:
            for key, child in value.items():
                walk(child, '%s.%s' % (path, key) if path else str(key))
        elif isinstance(value, list) and any(isinstance(v, (dict, list)) for v in value):
            for i, child in enumerate(value):
                walk(child, '%s[%d]' % (path, i))
        else:
            lines.append('%s: %s' % (path, value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)))
    walk(spec, '')
    return '\n'.join(lines)


_REFERENCE = re.compile(r'\b(?:rules?|sections?|steps?|items?|points?|paragraphs?|tables?|pages?|chapters?)\s+\d+(?:\.\d+)*(?:\s*(?:,|and|or|to|-)\s*\d+(?:\.\d+)*)*')


def figures(text):
    """The figures and code words of a text: numbers, also written as words; times of day; names with an
    underscore. Two texts that state the same limit share them, however the sentence around them is worded.
    The number of a rule that is referred to ("rule 30") and the number a sentence starts with are no figures,
    and the name of a tool is none either: a spec need not name the tool the runtime calls by itself."""
    low, found = _REFERENCE.sub(' ', re.sub(r'^\s*\d+[.)]\s+', '', str(text).lower())), set()
    for number in re.findall(r'\d+(?:[.,:]\d+)*', low):
        if ':' in number:
            found.add(':'.join(part.zfill(2) for part in number.split(':')))
            continue
        number = re.sub(r',(?=\d{3}(?!\d))', '', number).replace(',', '.')
        found.add(re.sub(r'\.0+$', '', number) if '.' in number else number)
    found.update(_WORDS[word] for word in re.findall(r'[a-z]+', low) if word in _WORDS)
    found.update(re.findall(r'[a-z][a-z0-9]*(?:_[a-z0-9]+)+', low))
    return found - set(TOOLS)


def _places(quote, documents):
    """Where a quotation stands in the documents, as (document number, offset), first place first."""
    quote = claude.squeeze(quote)
    if len(quote) < MIN_QUOTE:
        return []
    return [(i, text.find(quote)) for i, (_, text) in enumerate(documents) if quote in text]


def judge(items, briefs, spec, revised=False):
    """What each entry of the model's reply is worth, checked against the documents and the spec: one row per
    entry, with its status and the reason. `revised` says that the spec was revised for the omissions of an
    earlier reading: an omission that is not firm is then no longer reported as one, and not as covered either."""
    documents = [(name, claude.squeeze(text)) for name, text in briefs]
    text = view(spec)
    seen, have = claude.squeeze(text), figures(text)
    rows = []
    for item in items:
        claimed = str(item.get('status') or '').strip().upper()
        source = _places(item.get('source') or '', documents)
        passage = claude.squeeze(item.get('spec') or '')
        absent = sorted(figures(item.get('source') or '') - have)
        row = {'requirement': str(item.get('requirement') or '')[:400], 'kind': str(item.get('kind') or '')[:40], 'document': documents[source[0][0]][0] if source else None,
               'source': str(item.get('source') or '')[:600], 'claimed': claimed, 'spec': str(item.get('spec') or '')[:600], 'note': str(item.get('note') or '')[:400], 'blocking': False}
        if not source:
            status, why = UNVERIFIED, 'the sentence given as the source is not in the documents word for word'
        elif claimed == COVERED:
            if len(passage) < MIN_QUOTE or passage not in seen:
                status, why = UNVERIFIED, 'reported as covered, but the passage named is not in the spec word for word'
            elif absent:
                status, why = AMBIGUOUS, 'reported as covered, but the sentence has %s, which occurs nowhere in the spec' % ', '.join(absent)
            else:
                status, why = COVERED, 'both quotations are in the text word for word'
        elif claimed == OVERRIDDEN:
            later = [place for place in _places(item.get('replaced_by') or '', documents) if place > source[0]]
            if later:
                status, why = OVERRIDDEN, 'replaced by a sentence of %s that stands later' % documents[later[0][0]][0]
                row['replaced_by'] = str(item.get('replaced_by'))[:600]
            else:
                status, why = UNVERIFIED, 'reported as replaced, but no later text has the replacing sentence word for word; it stays a requirement'
        elif claimed == MISSING:
            if absent:
                status, why, row['blocking'] = MISSING, 'the sentence has %s, which occurs nowhere in the spec' % ', '.join(absent), True
            elif revised:
                status, why = UNVERIFIED, 'reported as missing; the spec was revised for it and validated; the revision was not audited again'
            else:
                status, why = MISSING, 'the reading of the model alone: no figure of the sentence is absent from the spec'
        elif claimed == AMBIGUOUS:
            status, why = AMBIGUOUS, 'the model calls the coverage partial or unclear'
        else:
            status, why = UNVERIFIED, 'the entry has no status that can be read'
        rows.append(dict(row, status=status, why=why))
    return rows


def unquoted(briefs, rows):
    """(lines of the documents that no confirmed source quotation touches, lines in all). Headings, table rules
    and lines under 40 characters are not counted. A measure of how much the model's list left unread."""
    sources = [claude.squeeze(row['source']) for row in rows if row['document']]
    total = left = 0
    for _, text in briefs:
        for line in text.splitlines():
            line = claude.squeeze(line)
            if len(line) < 40 or line.startswith(('#', '|')):
                continue
            total += 1
            left += not any(source in line or line in source for source in sources)
    return left, total


def summary(rows, briefs):
    counts = {status: sum(row['status'] == status for row in rows) for status in STATUSES}
    left, total = unquoted(briefs, rows)
    return {'requirements': len(rows), 'counts': counts, 'blocking': sum(row['blocking'] for row in rows), 'lines_unquoted': left, 'lines': total}


def audit(spec, briefs, openapi_text, client, check, trace=lambda line: None, system=None, usage=None, refused=None, revise=True):
    """Audit the coverage of a valid spec. Returns (spec, report): the spec, revised once if omissions were
    reported and the revision validated, and {'rows', 'summary', 'revised', 'readable'}. Raises what the audit
    call raises: a caller that has a valid spec keeps it and reports the audit as not made.

    `refused(seconds)` returns why no further call that needs so long can be made, or '' if it can."""
    documents = '\n\n'.join('<document index="%d" name="%s">\n%s\n</document>' % (i, name, text.strip()) for i, (name, text) in enumerate(briefs, 1))
    content = documents + '\n\n<specification>\n' + view(spec) + '\n</specification>'
    text, _ = claude.ask(client, PROMPT, [{'role': 'user', 'content': content}], 'audit the coverage of the mandatory rules of the documents', trace, usage)
    items = claude.read_list(text)
    rows = judge(items or [], briefs, spec)
    was_revised = False
    omissions = [row for row in rows if row['status'] == MISSING]
    if omissions and revise:
        why = refused(90) if refused else ''
        if why:
            trace('coverage: %d omission(s) reported; no revision: %s' % (len(omissions), why))
        else:
            findings = [{'item': 'missing', 'problem': 'The specification does not carry this requirement: %s %s' % (row['requirement'], row['note']), 'evidence': row['source']} for row in omissions]
            try:
                changed = claude.revise(spec, findings, briefs, openapi_text, client, check, trace, system, usage, 'revise the spec after the coverage audit')
            except Exception as exc:
                trace('coverage: the revision was not made, the spec is kept | %s: %s' % (type(exc).__name__, str(exc)[:200]))
                changed = spec
            if changed is not spec and changed != spec:
                spec, was_revised = changed, True
                rows = judge(items, briefs, spec, revised=True)
    report = {'rows': rows, 'summary': summary(rows, briefs), 'revised': was_revised, 'readable': items is not None}
    for row in rows:
        if row['status'] != COVERED:
            trace('coverage | %s%s | %s | %s | source (%s): %s' % (row['status'], ' (firm)' if row['blocking'] else '', row['requirement'][:200], row['why'], row['document'] or 'not found',
                                                                 claude.squeeze(row['source'])[:200]))
    return spec, report
