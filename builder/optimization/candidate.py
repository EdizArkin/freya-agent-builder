"""The builder's own next candidate: one narrow change to what the phone model is told, proposed from a failed case.

`propose` gives a model the current instructions, one failed case with what was said, what the judge wrote and
what the bank holds, and asks for ONE replacement of one passage. `apply` makes the candidate spec from it and
refuses everything that is not exactly that: a change outside the text the phone model reads, a passage that is
not there exactly once, a spec that no longer validates, or any difference in what the runtime enforces.

What a candidate can never touch: limits, dates, the security check, the callback window, the closing table, the
rule fields, tool names and fields. Those decide what the bank records, and they are the same in every candidate
as in the spec it came from (`digest`). Cases, checks and expected records are not inputs that can be changed:
they are only read.

What a candidate can never take away: a sentence of the passage that forbids, requires or limits something
(`GUARD`), a sentence the documents prescribe word for word (in quotation marks), the name of a tool or of a
value of the runtime. Such a sentence stays as it is, word for word; a candidate may add a sentence next to it.
This is a check of the words, not of their meaning: a sentence that is added can still be wrong, and only the
evaluation with its safety cases shows that (policy.decide).
"""
import copy
import json
import re

from builder.extraction import claude
from builder.spec.models import GUIDANCE_EVENTS, SpecError

# Where a candidate may change words: the three rule texts, the instruction for a moment after a tool result,
# and the line that says when to call a tool.
RULE_FIELDS = ('rules.always', 'rules.before_verification', 'rules.after_verification')
MAX_GROWTH = 600
MIN_KEPT = 0.6                  # a replacement shorter than this share of its passage removes more than it changes
# A sentence with one of these words forbids, requires, limits or orders something.
GUARD = re.compile(r"\b(?:never|only|must|not|no|nor|until|unless|before|after|without|cannot|always|exactly|once|confirm\w*|read[- ]?back|verif\w*|\w+n't)\b", re.I)
QUOTED = re.compile(r'"([^"\n]{3,})"|“([^”\n]{3,})”')
NAMED = re.compile(r'\b[a-z]+(?:_[a-z0-9]+)+\b|\{\{\s*\w+\s*\}\}|\b[A-Z][A-Z0-9]{2,}(?: [A-Z][A-Z0-9]+)*\b')

PROMPT = """You improve the instructions of a phone agent that makes outbound calls for a bank. A deterministic runtime already enforces every amount, date, limit and order; you cannot change those and must not try. You are given the agent's current instructions and ONE call that failed its check, with what was said, what the judge wrote and what the bank recorded.

Find the single most likely reason in the instructions for that failure, and propose ONE replacement of ONE passage that removes it. Change as little as possible. Do not add rules the bank's documents do not have, do not weaken any rule, do not mention the test, the judge or this case, and do not write anything that makes the agent skip a read-back, a confirmation, a security question or a prescribed sentence.

Your replacement must keep, word for word, every sentence of the passage that forbids, requires or limits something (never, only, must, not, until, before, after, once, confirm, read back, verify), every sentence in quotation marks, and every tool name, value name and {{variable}} of the passage. To change how such a sentence is followed, add a sentence next to it. A replacement that drops or rewrites one is refused.

If the failure does not come from the instructions (the judge misread, the customer went off script, the service failed, a tool call was refused or failed for a reason the instructions cannot change), say so and propose nothing. If changes are listed as already tried, they did not work: propose another one, or nothing.

Reply with one JSON object and nothing else:
{"case": "<the case id>", "hypothesis": "<one sentence: which words cause the failure and why>", "evidence": "<the words of the call that show it>",
 "field": "<one of the field names given>", "find": "<a passage of that field, copied exactly, long enough to occur only once>", "replace": "<what it becomes>",
 "risk": "<what this change could break>"}
or, when the instructions are not the cause: {"case": "<the case id>", "hypothesis": "<why>", "field": null}"""


class Rejected(ValueError):
    """A proposal that is not turned into a candidate, with the reason. `declined` is true when the model itself
    proposed nothing because it found that the instructions are not the cause."""

    def __init__(self, reason, declined=False):
        ValueError.__init__(self, reason)
        self.declined = declined


def fields(spec):
    """The texts a candidate may change, by name."""
    out = {name: (spec.get('rules') or {}).get(name.split('.', 1)[1]) for name in RULE_FIELDS}
    for event, text in (spec.get('guidance') or {}).items():
        if event in GUIDANCE_EVENTS or event.startswith('identity.hint.'):
            out['guidance.' + event] = text
    for tool, text in (spec.get('tool_descriptions') or {}).items():
        out['tool_descriptions.' + tool] = text
    return {name: text for name, text in out.items() if isinstance(text, str) and text.strip()}


def evidence(case):
    """One failed case as the model is shown it. `case` is a row of an evaluation: id, failed, messages, ledger, expected."""
    said = []
    for m in case.get('messages') or []:
        if m.get('role') in ('assistant', 'user') and str(m.get('message') or '').strip():
            said.append(('AGENT: ' if m['role'] == 'assistant' else 'CUSTOMER: ') + str(m['message']).strip())
    tools = ['%s%s -> %s' % (t.get('tool'), ' (refused by the policy check)' if t.get('blocked') else '', t.get('ended')) for t in case.get('tool_trace') or []]
    return '\n'.join(['<case id="%s">' % case.get('id'), 'WHY IT FAILED:'] + ['- ' + str(f) for f in case.get('failed') or []]
                     + ['BANK RECORDS: ' + json.dumps({k: (case.get('ledger') or {}).get(k) for k in ('outcome', 'reason', 'promises', 'notes', 'callbacks', 'transfers')}),
                        'EXPECTED: ' + json.dumps(case.get('expected')), 'TOOL CALLS: ' + (' > '.join(tools) or 'none'), 'CONVERSATION:'] + said + ['</case>'])


def squeeze(text):
    return re.sub(r'\s+', ' ', str(text)).strip()


def taken_away(find, replace):
    """What of the passage a replacement may not drop and does: guarded sentences, prescribed sentences, names."""
    after, lost = squeeze(replace), []
    for sentence in re.split(r'(?<=[.!?])\s+|\n+', find):
        sentence = squeeze(sentence)
        if sentence and GUARD.search(sentence) and sentence not in after:
            lost.append('the sentence "%s"' % sentence[:120])
    for match in QUOTED.finditer(find):
        if squeeze(match.group(1) or match.group(2)) not in after:
            lost.append('the prescribed words "%s"' % squeeze(match.group(1) or match.group(2))[:120])
    for name in sorted(set(NAMED.findall(find))):
        if name not in replace:
            lost.append('the name %s' % name)
    return lost


def apply(spec, proposal, check, digest):
    """The candidate spec of a proposal, and the record of what changed. Raises Rejected for anything else."""
    if not isinstance(proposal, dict) or not proposal.get('field'):
        raise Rejected('no change was proposed: ' + str((proposal or {}).get('hypothesis') if isinstance(proposal, dict) else proposal)[:300], declined=isinstance(proposal, dict))
    name, find, replace = proposal.get('field'), proposal.get('find'), proposal.get('replace')
    texts = fields(spec)
    if name not in texts:
        raise Rejected('%r is not a text the phone model reads; a candidate changes words, never what the runtime enforces' % (name,))
    if not isinstance(find, str) or not isinstance(replace, str) or len(find.strip()) < 12 or not replace.strip():
        raise Rejected('the proposal needs a passage to find and what to replace it with')
    if texts[name].count(find) != 1:
        raise Rejected('the passage to replace occurs %d times in %s, not once' % (texts[name].count(find), name))
    if find == replace or len(replace) - len(find) > MAX_GROWTH:
        raise Rejected('the replacement changes nothing, or adds more than %d characters: not one narrow change' % MAX_GROWTH)
    lost = taken_away(find, replace)
    if lost:
        raise Rejected('the replacement takes away what a candidate must keep word for word: ' + '; '.join(lost)[:500])
    if len(replace.strip()) < MIN_KEPT * len(find.strip()):
        raise Rejected('the replacement removes more than it changes: %d characters become %d' % (len(find.strip()), len(replace.strip())))
    # The only templates the instructions may carry are the variables a call starts with, as the spec names them.
    allowed = [re.escape(str(v)) for v in spec.get('call_variables') or [] if not str(v).startswith('rt_')]
    plain = re.sub(r'\{\{\s*(?:%s)\s*\}\}' % '|'.join(allowed), '', replace) if allowed else replace
    if '{{' in plain or '}}' in plain:
        raise Rejected('the replacement writes a template the instructions may not contain')
    candidate = copy.deepcopy(spec)
    group, key = name.split('.', 1)
    candidate[group][key] = texts[name].replace(find, replace)
    try:
        check(candidate)
    except SpecError as exc:
        raise Rejected('the candidate does not validate: ' + '; '.join(exc.problems)[:400])
    if digest(candidate) != digest(spec):
        raise Rejected('the candidate changes what the runtime enforces')
    for part in spec:
        if part not in ('rules', 'guidance', 'tool_descriptions') and candidate[part] != spec[part]:
            raise Rejected('the candidate changes %s' % part)
    return candidate, {'kind': 'wording', 'case': proposal.get('case'), 'hypothesis': proposal.get('hypothesis'), 'evidence': proposal.get('evidence'), 'risk': proposal.get('risk'),
                       'field': name, 'find': find, 'replace': replace}


def propose(spec, case, client, check, digest, trace=lambda line: None, usage=None, tried=()):
    """Ask the model for one change that addresses one failed case. Returns (candidate spec, what changed and why).

    `tried` lists the changes already made for this case on this spec that did not replace it: what was replaced
    by what, and how it ended. The model is shown them so that it does not propose them again."""
    texts = fields(spec)
    before = ''
    if tried:
        before = '\n\n<already_tried>\n' + '\n'.join('- in %s, "%s" became "%s": %s' % (t.get('field'), t.get('find'), t.get('replace'), t.get('outcome')) for t in tried) + '\n</already_tried>'
    content = ('<instructions>\n' + '\n\n'.join('<field name="%s">\n%s\n</field>' % (name, text) for name, text in texts.items()) + '\n</instructions>\n\n'
               + evidence(case) + before + '\n\nField names you may use: ' + ', '.join(texts) + '.')
    text, _ = claude.ask(client, PROMPT, [{'role': 'user', 'content': content}], 'propose one change for the failed case ' + str(case.get('id')), trace, usage)
    try:
        proposal = claude.parse_json(text)
    except ValueError as exc:
        raise Rejected('the reply is not one JSON object: ' + str(exc))
    if isinstance(proposal, dict) and proposal.get('case') not in (None, case.get('id')):
        raise Rejected('the proposal is about another case than the one it was given')
    return apply(spec, proposal, check, digest)
