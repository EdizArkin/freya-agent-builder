# M18 phase 2: what a pre-hook can see. Two feasibility probes

11 October 2026. Two live probes on one disposable workflow (`m18-hook-probe`), one simulation each. No model of Anthropic was called. The tool of the probe goes to the bank's read-only account call with a customer id that does not exist; the bank answered every request with "unknown customer_id", opened no session and recorded nothing. No existing workflow was changed. Nothing of the confirmation gate is implemented.

This report holds no platform id. The conversations are synthetic and are quoted only as far as needed.

## Result in short

| Question | Answer | Seen |
| --- | --- | --- |
| Can a pre-hook read the customer-turn counter? | Yes: `<node>.total_turns` and `<node>.current_turns`, as numbers | 8 hook records, equal to the engine's table each time |
| Does the counter move with customer turns? | Yes: plus one per customer turn, unchanged for calls inside one agent reply | 6 customer turns |
| Can a pre-hook remove or change body fields? | Yes: remove one, overwrite one, replace the whole body. The request that leaves is the changed one | 12 requests |
| Is `system.conversation` available in a pre-hook? | Yes: a list of messages, each with a `role` and a `content`, in order | 2 hook records, **one customer turn** |
| Is it current when the hook runs? | The customer's last message: yes. The agent's own words of the message that carries the tool call: **no**, they appear only at the next call | The same 2 records |
| Can it prove consent? | No. It can show that a read-back with the right values was said and that the customer answered afterwards, and it can refuse on words that are clearly not a yes | Reasoning from the structure; not tried on a full conversation |

The second probe went off its script after the first customer turn (the phone model returned an empty reply twice and the simulated customer ended the call). What it shows is solid for the structure and thin for everything that needs several turns. The one allowed simulation is used; nothing was repeated.

## 1. Probe 1: counter, body, state

One conversation node, one tool, a pre-hook that writes what it sees into variables. The phone model was told to call the tool twice after every customer message.

| Finding | Evidence |
| --- | --- |
| The counter is readable | `vars.get('probe.total_turns')` gave 1, 2, 2, 3, 4, 4, 5, 6 in the eight records that survived, each equal to the engine's own table at that moment |
| It is the same for two calls of one reply | Records 2 and 3 both have 2; records 5 and 6 both have 4 |
| Body fields can be removed | On odd runs the hook removed one field and overwrote another; on even runs it replaced the body with the call's own field alone. The engine's record of the request shows exactly that body each time |
| A variable written in one turn is there in the next | Every record saw the record before it |
| Two calls sent in one message do not see each other | 12 calls were made and the hook's own counter reached 8: where two calls started together, both read the old value and one update was lost. Known since M14.3, and it fails in the safe direction for a rule that needs two steps |
| Other things in a hook's variables | The arguments of the previous call of the tool (`<tool>_args.<field>`), the engine's date and time helpers, call metadata, and `system.conversation` |

## 2. Probe 2: `system.conversation`

The same workflow, updated (platform version 2). The phone model was told to say a read-back sentence and call the tool in the same message, then to call it once more. The simulated customer had five sentences: an offer, a yes, a change, a refusal, a yes. Only the first was reached.

What the two hook records hold:

| | First call: sent in the message that also carried the read-back | Second call: one model step later |
| --- | --- | --- |
| Type | list | list |
| Entries | 2 | 5 |
| Content | the opening line (`assistant`), the customer's sentence (`user`) | the same two, then the agent's read-back (`assistant`, text), the first tool call (`assistant`, with the tool's name and the arguments the model sent), and a fifth entry that the record cut off, by its position the answer to that call |
| Customer-turn counter | 1 | 1 |

Verified from this:

- **Structure.** A list in order of time. Each entry has a `role`; a spoken message has its text in `content`; a tool call is an `assistant` entry with the name and the arguments. It is the chat history in the form a model is given it.
- **Roles and order** are there. **Identifiers** are not, for spoken messages: only tool calls carry an id. A message can be addressed only by its position.
- **The customer's latest message is current.** The sentence said at 1.57 s was in the list of the hook that ran at 3.22 s.
- **The agent's words of the current message are not there yet.** The read-back was said in the same message as the first call and is missing from that call's list; it is in the list of the second call. A hook sees what was said before the message it belongs to.
- **Calls of one agent turn see different lists**, each longer than the one before. Consistent, not identical.
- **Tool calls and their arguments are in the list.** So a hook can find where a registration happened and what was said after it.

Not established, because the call ended early:

- that the list keeps growing the same way over many turns, and is never shortened or summarised (the node's default is to keep everything; not observed past five entries);
- what the entry of a tool answer looks like in full;
- how a customer turn that the speech layer cut off or misheard appears;
- anything about a voice call. Both probes are text chats.

## 3. What this evidence can and cannot enforce

The sequence the gate would look at, in the order the list has it: the registration call with its values; the agent's read-back; the customer's answer; then the request to record, whose hook is the one that looks.

| To enforce | Possible? | How, and how it can go wrong |
| --- | --- | --- |
| The read-back of the exact amount and date was said | Yes, as a text check | After the registration there must be an agent message that contains the amount and the date. The agent may say them in another form than the hook looks for ("two hundred and sixty", "this Friday"): the answer of the registration step therefore hands the agent the form to use, and the hook looks for that form. A read-back in other words is then refused although it was honest: a false refusal, never a false record |
| The product was named | Weakly | The spoken name is free text ("credit card ending ..."). With one product it adds nothing; with several it can be looked for |
| The customer answered after the read-back | Yes | A `user` entry after that agent message. The turn counter says the same thing independently |
| The answer was a yes | **Only as a word check** | An affirmative word and no negation. "Yes, but make it Monday", "yes?" as a question, "I guess", a yes to another question in the same turn, another language, a misheard word: each is a way to be wrong in one direction or the other |
| Refuse after a refusal or a change | Partly | A clear "no", or a number or day in the answer that differs from the registration, can stop the record. A change said in words the check does not know passes |
| The recorded values are those read back | Yes | By the registration, as in phase 1. This part does not depend on the conversation at all |

**Spoofing.** The `user` entries are the engine's record of what the customer said: the model cannot write them. A customer can say "yes" without meaning it, which is not spoofing but the limit of any consent given by voice. The agent's entries are the model's, and they are what was actually said, so a model cannot claim a read-back it did not speak. Not checked: whether a hook could write to the list itself. The runtime never would, and a hook is the builder's code.

**Stale state.** The list is read when the hook runs and was current for the customer's side. The hook must look only at what follows the latest registration for that product, never at an earlier yes.

**Concurrency.** A registration and a record sent in one message cannot pass: the record's hook sees neither the registration's variable nor a read-back, since the agent's words of that message are not in the list yet. Fail closed.

**False positives and false refusals.** Every condition on words can refuse an honest promise. Each such refusal costs a second read-back, which a customer hears and a judge may count. The words must therefore be few, the structural conditions strict, and the effect measured live before it is kept.

**What stays impossible.** Proving that a person understood and agreed. The gate can establish that the values were registered, that the agent said them, that the customer then spoke, and that the customer's words were not plainly a refusal or a change. That is evidence of a confirmation of a definite kind. It is not consent in any stronger sense, and on a voice call it rests on a transcription that was not examined.

## 4. Recommended gate for phase 2

The design of the phase 1 report (a `check_promise` step that registers the values on the bank's read-only call and returns the read-back; a `create_promise` that needs a registration) with three conditions on `create_promise` instead of two. A record is sent only if all hold:

1. **Registered.** The same product, amount to the cent and date to the day were registered by `check_promise` in an earlier turn. From the runtime's own variables. Verified mechanisms.
2. **The customer spoke since.** The turn counter is higher than at registration. **Mandatory**: the counter is readable, so a hook that cannot read it refuses. The fallback of phase 1 is dropped.
3. **The conversation agrees.** After the registration call in `system.conversation` there is an agent message that contains the amount and the date in the form the registration handed out, and after that a customer message that is not a plain refusal and names no other amount or date.

And one choice to make before implementing, because it decides how condition 3 fails:

| | If the conversation cannot be read, or has a shape the hook does not know | Consequence |
| --- | --- | --- |
| Strict | Refuse | Safest. A change of the platform's format, or a voice call that looks different, stops every promise |
| **Recommended** | Decide on conditions 1 and 2, and write into the state that 3 was not evaluated | The gate never gets weaker than the verified part, and every call says which conditions were in force |

Recommended because condition 3 rests on one observation of one text call. Conditions 1 and 2 rest on mechanisms seen many times. After a live run that shows condition 3 evaluated in every call of a regression, it can be made strict.

What the conversation check must not become: a list of phrases per brief or per language. It needs the amount and date as the registration printed them, a short set of refusal words for the language of the agent, and digits. Everything else stays with the phone model, as it is today.

Removing the model's fields before the account call is done by replacing the body, which probe 1 verified.

The acceptance criteria of phase 1 stand. To add:

- 22. A hook that cannot read the turn counter refuses the record.
- 23. With a conversation in the observed form: a registration, a read-back with the registered amount and date, a customer reply: recorded. The same without the read-back message: refused. With a read-back of another amount: refused. With a reply "no" and with a reply that names another amount: refused.
- 24. The read-back said in the same message as the request: refused, since it is not in the list yet.
- 25. An earlier yes, before the registration, does not count.
- 26. A conversation that is missing, not a list, or of another shape: decided on conditions 1 and 2, and the state says that condition 3 was not evaluated.
- 27. The stand-in engine of the tests gives hooks `system.conversation` in the observed form and the counters, and withholds the agent's words of the current message as the platform does.

## 5. Cost and evidence

| | Freya credits | Anthropic | Bank |
| --- | --- | --- | --- |
| Probe 1 | 0.055 | $0 | 12 read-only requests, all refused as unknown customer |
| Probe 2 | 0.111 | $0 | 2, the same |
| Together | 0.166 | $0 | nothing recorded |

Evidence, git-ignored, in `outputs/m18-hook-probe/`: the two workflows, the two calls with all engine events, and the ids. The hooks stored the shape of the conversation and at most a few hundred characters of it, all synthetic. The probe scripts were not committed: they are one-off and create things on Freya.

Left on Freya: the disposable workflow with two platform versions, two personas and two simulations (`M18 | Probe | Hook Evidence | v1`, `M18 | Probe | Conversation State | v2`). Nothing was deleted.

## Checks

Documentation only. No file under `builder/`, `tests/` or `benchmarks/harness/` changed. Both manifests verify, the exposure log has four entries, the prompt digests are those of M17. The phase 1 report carries a dated correction of its statement that speech cannot reach a hook.
