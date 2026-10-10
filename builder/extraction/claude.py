"""Brief + tool API -> agent-spec/1, by one Claude call and a bounded repair loop."""
import json
import re
import time

from builder.config import settings
from builder.spec.models import SpecError

SYSTEM = """You turn a bank's brief for an outbound phone call into a compact executable specification, `agent-spec/1`. A compiler turns your specification into a phone agent without any human step, so everything the agent must know has to be in your output, and everything in your output has to come from the documents you are given.

# How the phone agent is built from your output

The phone agent is a smaller language model that talks to the person. It is not careful with arithmetic, calendars or counting, so a deterministic runtime does that work for it:

- The call has two parts. Part 1 (`before_verification`): finding out who is on the line and checking identity. Part 2 (`after_verification`): everything after identity is fully verified. The runtime loads the account by itself between the two parts; the model never calls get_account.
- In part 1 the model can call only: verify_identity, schedule_callback, transfer_to_agent, close_call. In part 2 only: create_promise, add_note (if you enable it), transfer_to_agent, close_call. Do not write rules that need another tool in a part.
- The runtime computes, from your structured fields, a calendar of the coming weeks (business days, holidays, next business day), the allowed callback slots, and for every product the named values you define (amounts, dates, counts). The model sees them as facts blocks appended to your rule text. Your rule text must refer to these values by the names and labels you define, and must never restate a computation the runtime does ("the 12th business day after today"): say "the DEADLINE" instead.
- The runtime rejects a create_promise whose amount is outside `limits.min_amount..limits.max_amount`, whose date is in the past, after `limits.latest_date`, or not a business day, and a second promise for the same product. It rejects a schedule_callback outside the slots. It rejects a duplicate note. A rejected call records nothing and tells the model why.
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
                  "numeric_order": "DMY|MDY"}],    // only for date: how to read a date given as numbers
   "max_wrong": 3,                                 // wrong answers in total that end the check
   "response": {"verified": "...", "all_verified": "...", "wrong_left": "...", "hint": "..."}, // verify_identity response fields; leave out the ones the API does not have
   "hints": [{"value": "...", "counts_as_wrong": false, "instruction": "..."}]            // what to do for each hint value
 },
 "account": {
   "products_path": "...",                   // dot path to the product list in the get_account response
   "id_field": "...",                        // product field holding the product id
   "values": [ {"name": "snake_case", "label": "LABEL SHOWN TO THE MODEL", "expr": EXPR} ],  // computed per product, in order
   "limits": {"min_amount": "<value name>", "max_amount": "<value name>", "latest_date": "<value name>"}
 },
 "promise": {"product_param": "...", "amount_param": "...", "date_param": "...",   // body fields of create_promise
             "max_attempts": 2,              // create_promise calls per product before giving up on a failing service
             "business_day_only": true},
 "note": {"product_param": "...", "reason_param": "...", "forbidden_after_transfer": true},  // body fields of add_note. OMIT "note" entirely when the brief says add_note is not used.
 "callback": {"date_param": "...", "time_param": "...",                           // body fields of schedule_callback
              "include_today": true, "next_business_days": 1,                     // which days are allowed
              "start": "HH:MM", "end": "HH:MM",                                   // both included
              "after_now": true},                                                 // must be later than the current time
 "transfer": {"queue_param": "..."},
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
- {"kind":"const","value": ...}  a fixed number or word (a band name, a count of attempts).
- {"kind":"text","template":"savings plan {plan_code}"}  text with {product field} or {earlier value name} placeholders.
- {"kind":"field","field":"..."}  a numeric product field.
- {"kind":"percent","of":EXPR,"percent":30} and {"kind":"fixed_amount","value":50}.
- Dates: {"kind":"today"}, {"kind":"next_business_day"}, {"kind":"business_days_after","n":7} (the n-th business day after today), {"kind":"calendar_days_after","n":10}, {"kind":"last_business_day_of_week","week":0} and {"kind":"first_business_day_of_week","week":0} (week 0 is the current week, 1 the next; add "if_today_or_past":"following_week" when the brief says to move a week on if that day is today), {"kind":"end_of_month","months_ahead":0}, {"kind":"fixed_date","date":"YYYY-MM-DD"}, {"kind":"earliest","of":[EXPR,...]}, {"kind":"latest","of":[EXPR,...]}, {"kind":"named","name":"<earlier value>"}.
- Any date expression may add "roll":"next_business" or "previous_business", and "not_after":"<earlier value name>" to cap it.

`account.values` must contain a value named "label": the words the agent uses to name the product to the customer, exactly as the brief prescribes for each product type. Add one value for every amount, date, band and count the brief derives from a product: the full amount, the lowest acceptable amount, the latest acceptable date, the number of persuasion attempts, and each date the brief tells the agent to propose. Give each a short label in capitals and use that same label in your rule text.

## FLAG (for close.forced)

"transferred" (any transfer succeeded) or "transferred:<queue>"; "promise_recorded" (at least one promise recorded); "identity_failed" (wrong answers used up); "callback_booked"; "all_products_noted:<reason>" (no promise, every product has a note with that reason); "any_note" (no promise, at least one note). `reason_from` may be "last_transfer_queue" (uses queue_reasons) or "first_note_not:<reason>" (the note reason of the first product, in account order, whose note is not that reason). Order the rules by the brief's precedence.

## guidance events

Each value is one or two imperative sentences added to the runtime's own message: what to say (quote sentences the brief prescribes word for word) and which tool to call next with which values.
identity.next_question, identity.all_verified, identity.wrong, identity.failed, identity.unreadable, identity.service_error, identity.hint.<hint value>, promise.recorded_more (recorded, other products still open), promise.recorded_all (recorded, nothing open), promise.failed_retry, promise.failed_final, note.recorded_more, note.recorded_all, note.failed, callback.booked, callback.failed, transfer.done, transfer.failed.
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

    def __init__(self, model=None, max_tokens=32000, effort='high'):
        self.model = settings.extraction_model(model)
        self.max_tokens, self.effort = max_tokens, effort

    def complete(self, system, messages):
        import anthropic
        client = anthropic.Anthropic(timeout=900.0, max_retries=2)
        kwargs = dict(model=self.model, max_tokens=self.max_tokens, system=system, messages=messages,
                      thinking={'type': 'adaptive'}, output_config={'effort': self.effort})
        try:
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


class ReplayClient:
    """Offline stand-in: replays prepared replies in order."""

    def __init__(self, replies, model='replay'):
        self.replies, self.model, self.seen = list(replies), model, []

    def complete(self, system, messages):
        self.seen.append((system, messages))
        text = self.replies.pop(0)
        return text, {'model': self.model, 'input_tokens': 0, 'output_tokens': 0, 'stop_reason': 'end_turn'}, text


def extract(briefs, openapi_text, client, check, trace=lambda line: None, repairs=2):
    """Return a spec that `check` accepts. `check(spec)` raises SpecError with the problems it finds."""
    messages = [{'role': 'user', 'content': user_message(briefs, openapi_text)}]
    for attempt in range(repairs + 1):
        purpose = 'extract agent-spec/1 from the brief' if attempt == 0 else 'repair the spec after compiler errors (round %d)' % attempt
        started = time.time()
        text, usage, content = client.complete(SYSTEM, messages)
        trace('model call: %s | purpose: %s | %.0f s | in %s out %s tokens | stop %s' % (
            usage.get('model'), purpose, time.time() - started, usage.get('input_tokens'), usage.get('output_tokens'), usage.get('stop_reason')))
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
