"""Brief + tool API -> agent-spec/1.

One extractor with three strategies: `single` (one call and a bounded repair loop), `audit` (single, then one
targeted check of the high-risk content and at most one revision) and `double` (two independent extractions,
their differences adjudicated, at most one revision). All three return the same agent-spec/1 and pass the same
`check`; none of them can return a spec that `check` refused.
"""
import hashlib
import json
import re
import time

from builder.config import settings
from builder.spec.models import SpecError

_PROMPT = """You turn a bank's brief for an outbound phone call into a compact executable specification, `agent-spec/1`. A compiler turns your specification into a phone agent without any human step, so everything the agent must know has to be in your output, and everything in your output has to come from the documents you are given.

# How the phone agent is built from your output

The phone agent is a smaller language model that talks to the person. It is not careful with arithmetic, calendars or counting, so a deterministic runtime does that work for it:

- The call has two parts. Part 1 (`before_verification`): finding out who is on the line and checking identity. Part 2 (`after_verification`): everything after identity is fully verified. The runtime loads the account by itself between the two parts; the model never calls get_account.
- In part 1 the model can call only: verify_identity, schedule_callback, transfer_to_agent, close_call. In part 2 only: create_promise, add_note (if you enable it), transfer_to_agent, close_call. Do not write rules that need another tool in a part.
- The runtime computes, from your structured fields, a calendar of the coming weeks (business days, holidays, next business day), the allowed callback slots, and for every product the named values you define (amounts, dates, counts). The model sees them as facts blocks appended to your rule text. Your rule text must refer to these values by the names and labels you define, and must never restate a computation the runtime does ("the 12th business day after today"): say "the DEADLINE" instead.
- The runtime rejects a create_promise whose amount is outside `limits.min_amount..limits.max_amount`, whose date is in the past, before `limits.earliest_date`, after `limits.latest_date`, or not a business day, a second promise for the same product, and a promise for a product whose `limits.promise_allowed` is false. With `promise.resolve_first` it rejects a promise while a product that comes first is still open; with `promise.min_total` it rejects a promise once the required total can no longer be reached (it cannot take back a promise that is already recorded, so the rule must also be in your rule text); with `transfer.max_transfers` it rejects a further transfer. It rejects a schedule_callback outside the slots. It rejects a duplicate note. A rejected call records nothing and tells the model why.
- After every tool call the runtime tells the model what happened in plain words (answer wrong and how many tries are left, promise recorded and which products remain, service failed and whether to retry). Through `guidance` you add the brief's own instruction for that moment: what to say and which tool to call next.
- At close_call the runtime overrides the model's outcome and reason with the first `close.forced` rule whose condition holds. Use it for every outcome that follows mechanically from what was recorded. Outcomes that depend on what the person said stay with the model, guided by your rule text.

# Output

Return one JSON object and nothing else: no prose before or after, no code fence. Shape (comments are explanations, not part of the output):

{
 "spec_version": "agent-spec/1",
 "language": "English",                      // language the agent speaks
 "call_variables": ["..."],                  // every variable the brief says a call starts with
 "now_variable": "...",                      // the call variable that holds the current date and time
 "test_variables": {...},                    // one plausible example value per call variable, from the brief's examples
 "opening_line": "...",                      // the exact first thing the agent says, complete, obeying every opening rule. Use {{variable}} for call variables.
 "calendar": {"business_weekdays": [0,1,2,3,4], // Monday=0 ... Sunday=6
              "holidays": ["YYYY-MM-DD"], "week_start": 0},
 "identity": {
   "field_param": "...", "answer_param": "...",   // body fields of verify_identity: which question, and the answer
   "questions": [{"field": "...",                  // the value sent in field_param, from the tool API
                  "ask": "...",                    // how to ask it, as a noun phrase: "your date of birth"
                  "format": "date|digits|text",    // date: sent as YYYY-MM-DD; digits: digits only
                  "normalize": ["uppercase"],      // only when the brief prescribes the form of a text answer; any of uppercase, lowercase, remove_spaces, alphanumeric
                  "numeric_order": "DMY|MDY"}],    // only for date: how to read a date given as numbers
   "max_wrong": 3,                                 // wrong answers in total that end the check
   "response": {"verified": "...", "all_verified": "...", "wrong_left": "...", "hint": "..."}, // verify_identity response fields; leave out the ones the API does not have
   "hints": [{"value": "...", "counts_as_wrong": false, "instruction": "..."}]            // what to do for each hint value
 },
 "account": {
   "products_path": "...",                   // dot path to the product list in the get_account response
   "id_field": "...",                        // product field holding the product id
   "values": [ {"name": "snake_case", "label": "LABEL SHOWN TO THE MODEL", "expr": EXPR} ],  // computed per product, in order
   "limits": {"min_amount": "<value name>", "max_amount": "<value name>", "latest_date": "<value name>",
              "earliest_date": "<value name>",      // only if the brief sets an earliest promise date other than today
              "promise_allowed": "<value name>"}    // only if the brief forbids a promise for some products: a value that is true or false
 },
 "promise": {"product_param": "...", "amount_param": "...", "date_param": "...",   // body fields of create_promise
             "max_attempts": 2,              // create_promise calls per product before giving up on a failing service
             "business_day_only": true,
             // The next three only when the brief has such a rule:
             "resolve_first": [WHEN, ...],              // products matching an earlier WHEN must have a promise or a note before a promise on any later product
             "min_total": {"percent": 40, "of": "<amount value name>"},   // all promises of a call must add up to this share of the sum of that value over all products
             "allowed_after_transfer_to": ["<queue>"]}, // queues after which a promise may still be recorded although promises after a transfer are forbidden
 "note": {"product_param": "...", "reason_param": "...", "forbidden_after_transfer": true},  // body fields of add_note. OMIT "note" entirely when the brief says add_note is not used.
 "callback": {"date_param": "...", "time_param": "...",                           // body fields of schedule_callback
              "include_today": true, "next_business_days": 1,                     // which days are allowed
              "start": "HH:MM", "end": "HH:MM",                                   // both included
              "after_now": true},                                                 // must be later than the current time
 "transfer": {"queue_param": "...", "max_transfers": 1},      // max_transfers only if the brief limits the number of transfers in a call
 "close": {
   "outcome_param": "...", "reason_param": "...",
   "outcomes": {"<outcome>": ["<reason>", "..."]},     // the brief's complete outcome table; [] for an outcome without reason
   "queue_reasons": {"<queue>": "<reason>"},           // the close reason that belongs to each transfer queue
   "forced": [ {"if": FLAG, "unless": FLAG, "outcome": "...", "reason": "...", "reason_from": "..."} ]   // first match wins
 },
 "guidance": {"<event>": "..."},
 "tool_descriptions": {"<tool>": "..."},     // one line per tool the model can call: what it does and exactly when to CALL it
 "rules": {"always": "...", "before_verification": "...", "after_verification": "..."},
 "unsupported": ["..."]                      // mandatory policy you could express neither in structure nor in rule text. Normally empty.
}

## EXPR

An expression is evaluated for one product. Kinds:
- {"kind":"by","cases":[{"when":WHEN,"expr":EXPR}, ...]}  first case whose WHEN matches. WHEN is {"<product field>": value | [values] | {"min":n,"max":n}}; {} always matches. Cover every product the API can return.
- {"kind":"by_time","cases":[{"when":TIME,"expr":EXPR}, ...]}  first case whose TIME matches the call's clock. TIME is {"from":"HH:MM"} (at or after), {"after":"HH:MM"} (later than), {"until":"HH:MM"} (up to and including), {"before":"HH:MM"}, or a combination; {} always matches. Use it only where a brief's rule depends on the time of day, and choose "after" or "from" by the brief's wording.
- {"kind":"const","value": ...}  a fixed number or word (a band name, a count of attempts), or true / false for "promise_allowed".
- {"kind":"text","template":"savings plan {plan_code}"}  text with {product field} or {earlier value name} placeholders.
- {"kind":"field","field":"..."}  a numeric product field.
- {"kind":"percent","of":EXPR,"percent":30} and {"kind":"fixed_amount","value":50}.
- Dates: {"kind":"today"}, {"kind":"next_business_day"}, {"kind":"business_days_after","n":7} (the n-th business day after today), {"kind":"calendar_days_after","n":10}, {"kind":"last_business_day_of_week","week":0} and {"kind":"first_business_day_of_week","week":0} (week 0 is the current week, 1 the next; add "if_today_or_past":"following_week" when the brief says to move a week on if that day is today), {"kind":"end_of_month","months_ahead":0}, {"kind":"fixed_date","date":"YYYY-MM-DD"}, {"kind":"earliest","of":[EXPR,...]}, {"kind":"latest","of":[EXPR,...]}, {"kind":"named","name":"<earlier value>"}.
- Any date expression may add "roll":"next_business" or "previous_business", and "not_after":"<earlier value name>" to cap it.

`account.values` must contain a value named "label": the words the agent uses to name the product to the customer, exactly as the brief prescribes for each product type. Add one value for every amount, date, band and count the brief derives from a product: the full amount, the lowest acceptable amount, the latest acceptable date, the number of persuasion attempts, and each date the brief tells the agent to propose. Give each a short label in capitals and use that same label in your rule text.

## FLAG (for close.forced)

"transferred" (any transfer succeeded) or "transferred:<queue>"; "promise_recorded" (at least one promise recorded); "identity_failed" (wrong answers used up); "callback_booked"; "all_products_noted:<reason>" (no promise, every product has a note with that reason); "any_note" (no promise, at least one note); "note_recorded:<reason>" (some product has a note with that reason, whether or not promises were recorded). `reason_from` may be "last_transfer_queue" (uses queue_reasons) or "first_note_not:<reason>" (the note reason of the first product, in account order, whose note is not that reason). Order the rules by the brief's precedence.

## guidance events

Each value is one or two imperative sentences added to the runtime's own message: what to say (quote sentences the brief prescribes word for word) and which tool to call next with which values.
identity.next_question, identity.all_verified, identity.wrong, identity.failed, identity.unreadable, identity.service_error, identity.hint.<hint value>, promise.recorded_more (recorded, other products still open), promise.recorded_all (recorded, nothing open), promise.failed_retry, promise.failed_final, promise.not_allowed (a promise was tried for a product that may not have one), note.recorded_more, note.recorded_all, note.failed, callback.booked, callback.failed, transfer.done, transfer.failed.
Write guidance only where the brief has something specific for that moment.

## rules

The three rule texts are the only policy the phone model reads. Together they must carry EVERY rule, exception, prohibition, precedence statement and prescribed sentence of the brief that the model has to act on. A rule left out is a failed call.
- "always": who the agent is, how to address the person, and rules that hold in both parts (what to answer to common questions, what may never be offered, how every call ends).
- "before_verification": who is on the line, what may not be said before verification, every third-party branch, identity questions and refusals, and the exact close_call outcome and reason of each branch.
- "after_verification": the order of the conversation, how to state the debt, every amount and date rule, persuasion steps and their limits, every other thing customers say, confirmation and recording, and the exact close_call outcome and reason of each ending. If a customer can have several products, say exactly how the conversation runs across them.
Write them as direct instructions to the model ("Ask ...", "If the customer ..., then ..."), grouped under short headings, in the order the call runs. Name tools exactly and say when to call them. Quote prescribed sentences exactly. State counts and limits ("once", "at most two") explicitly. Spell out how the brief wants spoken dates understood ("next Friday") in terms of the calendar's THIS WEEK / NEXT WEEK sections. Do not mention nodes, steps of a workflow, JSON or this specification.

# Reading the documents

- Documents are given in order. A later document amends an earlier one; where they conflict, the later one wins. A memo, mail or code list is as binding as a clean specification. Produce one consolidated specification with amendments already applied.
- Take every tool field name, response field name and allowed value from the tool API document, never from memory or from an example in these instructions.
- Never invent a rule, number, date or sentence. If the documents do not say, leave the optional field out.
- Keep the output compact: no commentary, no repetition of one rule in several places beyond what is needed.
"""

STRUCTURING = """Every rule of the documents goes to exactly one of three places.
- A structured field, when the rule fixes something the runtime computes, counts or checks: an amount, a date, a window, a limit, a count of attempts, which products may get a promise, in which order, an outcome that follows from what was recorded. Prefer this whenever a field fits: only a structured rule holds whatever the phone model does. Put it in the rule text as well only where the model has to say or propose it.
- The rule text, when the rule is about what to say, in which order, how often, or how to react to what the person says.
- `unsupported`, only when the rule is mandatory, something would have to enforce it, and no structured field can carry it. Name the rule there in one sentence, and still describe it in the rule text so that the phone model follows it. Never drop such a rule silently, and never bend a field to a meaning it does not have."""

# The prompt in named sections. The first part defines agent-spec/1 itself: what validation and the compiler
# implement. It is fixed. The three sections after it are guidance on how to read and write, and are the only
# parts a prompt optimisation may change.
MUTABLE = ('rules_writing', 'structuring', 'reading')
_FIXED_V1, _rest = _PROMPT.split('## rules\n\n', 1)
_rules_writing, _reading = _rest.split('# Reading the documents\n\n', 1)
SECTIONS = {'rules_writing': _rules_writing.strip(), 'structuring': STRUCTURING, 'reading': _reading.strip()}

# The prompt exists in versions. 'v1' is the prompt of M15, word for word: the one its selection and holdout
# figures were measured with (digest 083c66052cdf131b). 'v2' describes the four rule kinds the schema has since
# M16 and changes nothing else: each entry of _V2_EDITS replaces one passage of the fixed part of v1 with the
# same passage extended. The three mutable sections are the same in every version.
_V2_FIELDS = """## Promise and callback rules with their own field

Four kinds of rule are checked by the runtime on every call when you write their field. Write a field only when a document states that rule, with the number, day or time the document gives. Never derive a step, an amount, a date or a time that no document states, and leave the field out when the documents do not have the rule.
- `promise.amount_step`: the documents accept amounts only in whole multiples of a stated amount. Write that amount as a positive number with at most two decimals. The lowest and the highest amount of the product still apply. `amount_step_except_max` is true only when the documents say that the full amount is accepted although it is no such multiple; it needs `limits.max_amount`.
- `promise.min_amount_by_date`: the documents make the lowest acceptable amount depend on the date of the promise: a promise dated up to and including a certain day needs at least a certain amount, and a lower amount is accepted only with a later date. `on_or_before` is "today" or the name of a date value of `account.values`; `min_amount` is the name of an amount value of `account.values`. Add a value to `account.values` when the day or the amount has none yet. One entry per rule. The ordinary lowest amount stays in `limits.min_amount`.
- `promise.same_date`: true only when the documents require every promise of one call to carry the same date. The runtime then takes the first promise for the first open product of the list (after `resolve_first`) and refuses a later promise with another date, so say in the rule text that one date for all products is fixed with the person before the first promise is recorded. Leave it out when each product may have its own date.
- `callback.closed`: the documents name a period of the day in which no callback can be booked although it lies between `start` and `end`. Each period is a TIME as for by_time and must test at least one limit: "from" and "until" include their minute, "after" and "before" do not; choose by the wording of the documents. A period holds on every allowed day.
A rule of one of these kinds that its field cannot carry as described (a step that differs by product, a closed period on some weekdays only, one date for some of the products only) is not written into the field: name it under `unsupported`. Every rule you write into one of these fields also goes into the rule text, with the labels of the values it uses, so that the phone model proposes what the runtime accepts.

"""
_V2_EDITS = (
    ('with `transfer.max_transfers` it rejects a further transfer. It rejects a schedule_callback outside the slots.',
     'with `transfer.max_transfers` it rejects a further transfer. With `promise.amount_step` it rejects an amount that is not a whole multiple of the step; '
     'with `promise.min_amount_by_date` an amount below what the date of the promise requires; with `promise.same_date` a promise whose date differs from the date '
     'already recorded in the call. It rejects a schedule_callback outside the slots or inside a `callback.closed` period.'),
    ('             "allowed_after_transfer_to": ["<queue>"]}, // queues after which a promise may still be recorded although promises after a transfer are forbidden\n',
     '             "allowed_after_transfer_to": ["<queue>"],  // queues after which a promise may still be recorded although promises after a transfer are forbidden\n'
     '             // The next four only when a document states such a rule; see "Promise and callback rules with their own field":\n'
     '             "amount_step": <number>,                   // amounts are recorded only in whole multiples of this amount\n'
     '             "amount_step_except_max": true,            // only with amount_step: the highest amount is accepted although it is no such multiple\n'
     '             "min_amount_by_date": [{"on_or_before": "today" | "<date value name>", "min_amount": "<amount value name>"}],  // a promise dated on or before that day needs at least that amount\n'
     '             "same_date": true},                        // all promises of one call carry one and the same date\n'),
    ('              "after_now": true},                                                 // must be later than the current time\n',
     '              "after_now": true,                                                  // must be later than the current time\n'
     '              "closed": [TIME, ...]},                                             // only when a document names times of day inside the window at which no callback can be booked\n'),
    ('## FLAG (for close.forced)\n', _V2_FIELDS + '## FLAG (for close.forced)\n'),
)


def _extended(text, edits):
    for old, new in edits:
        if text.count(old) != 1:
            raise ValueError('a prompt version replaces a passage that is not in the prompt exactly once: ' + old[:60])
        text = text.replace(old, new)
    return text


PROMPT_VERSIONS = {'v1': _FIXED_V1, 'v2': _extended(_FIXED_V1, _V2_EDITS)}
PROMPT_VERSION = 'v2'               # what a build uses unless it asks for another
FIXED = PROMPT_VERSIONS[PROMPT_VERSION]


def system_prompt(sections=None, version=None):
    """The system prompt of one version, with some of its mutable sections replaced."""
    unknown = [name for name in (sections or {}) if name not in MUTABLE]
    if unknown:
        raise ValueError('only these sections of the prompt may be replaced: %s; got %s' % (', '.join(MUTABLE), ', '.join(unknown)))
    if (version or PROMPT_VERSION) not in PROMPT_VERSIONS:
        raise ValueError('unknown prompt version %r; one of %s' % (version, ', '.join(PROMPT_VERSIONS)))
    text = dict(SECTIONS, **(sections or {}))
    return (PROMPT_VERSIONS[version or PROMPT_VERSION] + '## rules\n\n' + text['rules_writing'].strip() + '\n\n## Where a rule goes\n\n' + text['structuring'].strip()
            + '\n\n# Reading the documents\n\n' + text['reading'].strip() + '\n')


def prompt_digest(sections=None, version=None):
    """The name of a prompt in traces, records and reports: the first 16 hex digits of its SHA-256."""
    return hashlib.sha256(system_prompt(sections, version).encode('utf-8')).hexdigest()[:16]


SYSTEM = system_prompt()

REPAIR = """The specification cannot be compiled. Problems found by the compiler:

{problems}

Return the complete corrected JSON object, and nothing else."""


def user_message(briefs, openapi_text):
    parts = ['<tool_api>\n' + openapi_text.strip() + '\n</tool_api>']
    for i, (name, text) in enumerate(briefs, 1):
        parts.append('<document index="%d" name="%s">\n%s\n</document>' % (i, name, text.strip()))
    parts.append('Write the agent-spec/1 JSON object for a phone agent that follows these documents.')
    return '\n\n'.join(parts)


def parse_json(text):
    text = text.strip()
    fence = re.search(r'```(?:json)?\s*(\{.*\})\s*```', text, re.S)
    if fence:
        text = fence.group(1)
    start, end = text.find('{'), text.rfind('}')
    if start < 0 or end <= start:
        raise ValueError('the reply contains no JSON object')
    return json.loads(text[start:end + 1])


class AnthropicClient:
    """One streamed Messages call per turn. Credentials come from the environment, never from arguments."""

    def __init__(self, model=None, max_tokens=32000, effort='high', fallback=True):
        self.model = settings.extraction_model(model)
        self.max_tokens, self.effort, self.fallback = max_tokens, effort, fallback

    def complete(self, system, messages):
        import anthropic
        client = anthropic.Anthropic(timeout=900.0, max_retries=2)
        kwargs = dict(model=self.model, max_tokens=self.max_tokens, system=system, messages=messages,
                      thinking={'type': 'adaptive'}, output_config={'effort': self.effort})
        try:
            if not self.fallback:       # a measurement wants the model it asked for, and its price
                raise TypeError('no fallback')
            # A safety classifier can decline a request; let the API route it instead of failing the build.
            with client.beta.messages.stream(betas=['server-side-fallback-2026-07-01'], fallbacks='default', **kwargs) as stream:
                message = stream.get_final_message()
        except (TypeError, anthropic.BadRequestError):
            with client.messages.stream(**kwargs) as stream:
                message = stream.get_final_message()
        if message.stop_reason == 'refusal':
            raise RuntimeError('the model declined the request')
        if message.stop_reason == 'max_tokens':
            raise RuntimeError('the model stopped at max_tokens=%d; raise --max-tokens' % self.max_tokens)
        text = ''.join(b.text for b in message.content if b.type == 'text')
        usage = {'model': message.model, 'input_tokens': message.usage.input_tokens,
                 'output_tokens': message.usage.output_tokens, 'stop_reason': message.stop_reason}
        return text, usage, message.content


def sdk_installed():
    """Whether this Python can make the call at all. A paid command asks before it starts: a meter charges a call
    that ends in an error at its most, and a call that fails on the import was never sent."""
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return True


class ReplayClient:
    """Offline stand-in: replays prepared replies in order."""

    def __init__(self, replies, model='replay'):
        self.replies, self.model, self.seen = list(replies), model, []

    def complete(self, system, messages):
        self.seen.append((system, messages))
        text = self.replies.pop(0)
        return text, {'model': self.model, 'input_tokens': 0, 'output_tokens': 0, 'stop_reason': 'end_turn'}, text


def ask(client, system, messages, purpose, trace, usage=None):
    """One model call, traced with its model and purpose and added to `usage` when a list is given."""
    started = time.time()
    text, used, content = client.complete(system, messages)
    seconds = time.time() - started
    trace('model call: %s | purpose: %s | %.0f s | in %s out %s tokens | stop %s' % (
        used.get('model'), purpose, seconds, used.get('input_tokens'), used.get('output_tokens'), used.get('stop_reason')))
    if usage is not None:
        usage.append(dict(used, purpose=purpose, seconds=round(seconds, 1)))
    return text, content


def extract(briefs, openapi_text, client, check, trace=lambda line: None, repairs=2, system=None, usage=None):
    """Return a spec that `check` accepts. `check(spec)` raises SpecError with the problems it finds."""
    messages = [{'role': 'user', 'content': user_message(briefs, openapi_text)}]
    for attempt in range(repairs + 1):
        purpose = 'extract agent-spec/1 from the brief' if attempt == 0 else 'repair the spec after compiler errors (round %d)' % attempt
        text, content = ask(client, system or SYSTEM, messages, purpose, trace, usage)
        try:
            candidate = parse_json(text)
            check(candidate)
            return candidate
        except SpecError as exc:
            problems = exc.problems
        except ValueError as exc:
            problems = ['the reply is not one valid JSON object: ' + str(exc)]
        trace('spec rejected: ' + ' | '.join(problems)[:1500])
        if attempt == repairs:
            raise SpecError(problems)
        messages += [{'role': 'assistant', 'content': content},
                     {'role': 'user', 'content': REPAIR.format(problems='\n'.join('- ' + p for p in problems))}]


# ---------------------------------------------------------------- strategies

STRATEGIES = ('single', 'audit', 'double')

AUDIT = """You check a specification that another model wrote from a bank's documents for an outbound phone call. You are given the documents, the tool API, and a list of what the specification makes the phone agent do, worked out by code: limits and dates per kind of product, the security check, the callback window, transfers, the closing table and the order of its rules.

Compare every item with the documents. Later documents amend earlier ones. Look in particular for: a number, limit or deadline that differs from the documents; an amendment that was not applied; an exception or a product type that is missing; a boundary on the wrong side (up to, more than, both included); a wrong order of precedence at the close; a rule of the documents that appears nowhere in the list although code would have to enforce it.

Reply with one JSON array and nothing else. One object per defect you are sure of:
{"item": "<the item of the list, or 'missing'>", "problem": "<what is wrong, in one sentence>", "evidence": "<the sentence of the documents that shows it, copied word for word>", "fix": "<what the item should be>"}
The evidence must be an exact quotation from the documents; a defect without one is ignored. If nothing is wrong, reply with []."""

ADJUDICATE = """Two specifications were written independently from the same bank documents for an outbound phone call. Code worked out what each makes the phone agent do and found the items below on which they differ. You are given the documents, the tool API, and for each item the value of specification A and of specification B.

Decide each item from the documents alone. Later documents amend earlier ones.

Reply with one JSON array and nothing else, one object per item:
{"item": "<the item>", "right": "A" | "B" | "neither", "evidence": "<the sentence of the documents that decides it, copied word for word>", "fix": "<only for neither: what the item should be>"}
The evidence must be an exact quotation from the documents; a decision without one is ignored."""

REVISE = """A check of your specification against the documents found these defects. Each comes with the sentence of the documents that shows it.

{findings}

Return the complete corrected JSON object, and nothing else. Change only what these defects require."""


def squeeze(text):
    return re.sub(r'\s+', ' ', str(text)).strip().casefold()


def parse_list(text):
    """The JSON array in a reply, or [] when there is none: a check that cannot be read finds nothing."""
    start, end = text.find('['), text.rfind(']')
    if start < 0 or end <= start:
        return []
    try:
        found = json.loads(text[start:end + 1])
    except ValueError:
        return []
    return [f for f in found if isinstance(f, dict)] if isinstance(found, list) else []


def quoted(findings, briefs, openapi_text):
    """Only the findings whose evidence is word for word in the documents. The rest is not acted on."""
    source = squeeze(openapi_text + ' ' + ' '.join(text for _, text in briefs))
    return [f for f in findings if len(squeeze(f.get('evidence', ''))) >= 12 and squeeze(f['evidence']) in source]


def render(digest):
    return '\n'.join('- %s: %s' % (item, json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)) for item, value in digest.items())


def revise(spec, findings, briefs, openapi_text, client, check, trace, system, usage, purpose):
    """One revision of a valid spec. A revision that `check` refuses is dropped and the spec is kept as it was."""
    listed = '\n'.join('- %s: %s Evidence: "%s"%s' % (f.get('item'), f.get('problem') or 'wrong.', f.get('evidence'), (' Should be: ' + str(f['fix'])) if f.get('fix') else '')
                       for f in findings)
    messages = [{'role': 'user', 'content': user_message(briefs, openapi_text)},
                {'role': 'assistant', 'content': json.dumps(spec, ensure_ascii=False)},
                {'role': 'user', 'content': REVISE.format(findings=listed)}]
    text, _ = ask(client, system or SYSTEM, messages, purpose, trace, usage)
    try:
        candidate = parse_json(text)
        check(candidate)
        return candidate
    except (SpecError, ValueError) as exc:
        trace('revision dropped, the spec before it is kept: ' + str(exc)[:600])
        return spec


def run(strategy, briefs, openapi_text, client, check, trace=lambda line: None, digest=None, system=None, usage=None, repairs=2):
    """Extract with one of STRATEGIES. `digest(spec)` or `digest(spec, other)` returns what a spec makes the agent do,
    as {item: value}; the audit and the comparison work on it, never on the JSON as written."""
    if strategy not in STRATEGIES:
        raise ValueError('unknown extraction strategy %r; one of %s' % (strategy, ', '.join(STRATEGIES)))
    first = extract(briefs, openapi_text, client, check, trace, repairs, system, usage)
    if strategy == 'single':
        return first
    if digest is None:
        raise ValueError('the %s strategy needs a digest of the spec' % strategy)
    documents = user_message(briefs, openapi_text).rsplit('\n\n', 1)[0]

    if strategy == 'audit':
        content = documents + '\n\n<what_the_specification_does>\n' + render(digest(first)) + '\n</what_the_specification_does>'
        text, _ = ask(client, AUDIT, [{'role': 'user', 'content': content}], 'audit the high-risk content of the spec against the documents', trace, usage)
        findings = quoted(parse_list(text), briefs, openapi_text)
        trace('audit: %d finding(s) with evidence from the documents' % len(findings))
        for f in findings:
            trace('finding: %s | %s | evidence: %s' % (f.get('item'), str(f.get('problem'))[:300], squeeze(f.get('evidence', ''))[:300]))
        if not findings:
            return first
        return revise(first, findings, briefs, openapi_text, client, check, trace, system, usage, 'revise the spec after the audit')

    second = extract(briefs, openapi_text, client, check, trace, repairs, system, usage)
    a, b = digest(first, second), digest(second, first)
    differing = [item for item in list(a) + [i for i in b if i not in a] if a.get(item) != b.get(item)]
    trace('double extraction: %d item(s) differ' % len(differing))
    if not differing:
        return first
    for item in differing:              # kept, so that a disagreement can be looked at afterwards without another call
        trace('differs: %s | A: %s | B: %s' % (item, json.dumps(a.get(item), sort_keys=True, default=str), json.dumps(b.get(item), sort_keys=True, default=str)))
    listed = '\n'.join('- %s\n  A: %s\n  B: %s' % (item, json.dumps(a.get(item), sort_keys=True, default=str), json.dumps(b.get(item), sort_keys=True, default=str)) for item in differing)
    content = documents + '\n\n<items_on_which_they_differ>\n' + listed + '\n</items_on_which_they_differ>'
    text, _ = ask(client, ADJUDICATE, [{'role': 'user', 'content': content}], 'adjudicate the differences of two extractions', trace, usage)
    decided = [d for d in quoted(parse_list(text), briefs, openapi_text) if d.get('item') in differing and d.get('right') in ('A', 'B', 'neither')]
    wins = {side: sum(d['right'] == side for d in decided) for side in ('A', 'B')}
    # The spec that was right more often is kept whole, so that its rule text and its structure still belong together.
    winner, other = (second, 'A') if wins['B'] > wins['A'] else (first, 'B')
    trace('adjudication: %d decided with evidence, A right %d, B right %d' % (len(decided), wins['A'], wins['B']))
    for d in decided:
        trace('decided: %s | right: %s | evidence: %s' % (d['item'], d['right'], squeeze(d.get('evidence', ''))[:300]))
    against = [dict(d, problem='the other extraction has %s' % json.dumps((a if other == 'A' else b).get(d['item']), sort_keys=True, default=str),
                    fix=d.get('fix') or (a if other == 'A' else b).get(d['item'])) for d in decided if d['right'] in (other, 'neither')]
    if not against:
        return winner
    return revise(winner, against, briefs, openapi_text, client, check, trace, system, usage, 'revise the spec after the adjudication')
