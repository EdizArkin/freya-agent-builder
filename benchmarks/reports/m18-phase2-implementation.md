# M18 phase 2: the confirmation gate. Implementation

11 October 2026. Offline only: no model call, no Freya call, no deployment, no bank. Built on `12206e7`. The design is `m18-phase1-safety-design.md`; what a hook can see on the platform is `m18-phase2-conversation-probe.md`.

**Nothing here is verified live.** The gate is tested against a stand-in engine that gives hooks what two probe calls showed the platform gives them. Whether a phone model takes the path the gate allows, what that costs in score and time, and whether Freya accepts the larger workflows, are questions for phase 5.

## What is enforced

`create_promise` reaches the bank only if every one of these holds. Each is checked in the pre-hook, after all checks that existed before (identity, product, limits, dates, order, the four M16 rules), and each refusal detaches the request as every other refusal does.

| # | Condition | Evidence it rests on |
| --- | --- | --- |
| 1 | The values were registered by `check_promise`: the same customer, product, amount to the cent, date to the day | A variable only the runtime's hooks write, named with the id of the call |
| 2 | The registration is from an earlier customer turn, and not more than 6 customer turns old | The engine's count of customer turns in the node; a hook that cannot read it refuses |
| 3 | The conversation can be read in the form the engine gives it | `system.conversation`; missing, not a list, or an entry of another shape: refused |
| 4 | After the registration the agent said a message that states the amount as a number and the date with its day and month (or in ISO form) | The agent's own messages in that list, from the position the list had at registration |
| 5 | The customer answered that message, and nothing else the agent said lies between the read-back and the answer | Order of the entries |
| 6 | The answer contains a word of agreement, and neither it nor any later customer turn contains a refusal, a hesitation, a change, a question, or an amount, weekday or month that was not in the read-back | The customer's messages after the read-back |

`check_promise` runs the same checks as `create_promise` from one shared function, registers what passes, and replaces its request by the account request of the tool API: the call's own fields and nothing the model sent. Its answer tells the agent what to read back. It records nothing, and the operation it travels on cannot.

What follows from the conditions:

- A request to skip the read-back cannot produce a record: there is no registration (1), or no read-back after it (4), or no answer to it (5).
- Check and record in one customer turn: refused (2). In one message: refused, since the record's hook sees neither the registration nor the turn.
- A yes anywhere else in the call does not count: only the customer's turns after the read-back are read, and the first of them must agree.
- A yes to another question: refused (5).
- "Yes, but change the date", "no", "maybe", a question: refused (6).
- Another amount, date or product than was checked: refused (1). New values need a new check, and the earlier yes does not carry over (2, 4, 5).
- A flag from the model that says "confirmed" is not a field of any request and is not looked at.
- A failing promise service is retried without a new read-back; the registration stands.
- Anything unreadable refuses. There is no mode in which a condition is skipped.

## What is not established

Stated once, so that nobody reads more into the gate than it does.

- **Consent.** The gate shows that definite things happened in a definite order with definite values, and that the customer's words were not plainly something other than a yes. It does not show that a person understood or meant it. It is evidence, not proof, and no cryptographic property is claimed anywhere.
- **The words.** Condition 6 is a word check, in English. It is conservative in the direction of refusing.
- **Voice.** Everything rests on what the engine lists as said. On a voice call that is a transcription. Not examined.
- **One observation.** The form of `system.conversation` was seen in one call with one customer turn. The gate depends on: a list; entries with a `role`; spoken text in `content`. It does not depend on the entry of a tool's answer, on ids, or on the list being complete before the registration. If the platform shortens or summarises the list, the read-back is not found and the gate refuses.
- **A hook the platform does not run.** No gate at all, as for every check of the runtime. Unchanged.

## False refusals and false records

False refusals (an honest promise is refused, and the agent has to read back again):

| Case | Why |
| --- | --- |
| The agent reads back in words the hook does not find: "the full amount tomorrow", an amount spelled out, a date without its month | Condition 4 needs the number and the day with the month. The check's answer hands the agent that form |
| The agent adds another question to, or after, the read-back | Condition 5 |
| The customer agrees without one of the listed words ("go ahead", "do it", "mm-hm", "fine") | Condition 6: twelve words of agreement |
| The customer agrees and adds something harmless that contains a listed word ("yes, no problem", "yes, if that's all") | Condition 6: any refusal or hesitation word refuses |
| The customer's turn is cut off before the yes | Seen in 7 of 165 customer turns in M17 |
| The agent does not use `check_promise` and reads back on its own | No registration. It has to check and read back a second time |
| An agent in another language | No yes is recognised: no promise can be recorded. The build warns. All briefs so far are English |

False records (a promise is recorded although it should not be):

| Case | Why it can still happen |
| --- | --- |
| The customer says "yes" to the read-back and does not mean it, or did not hear it | Not observable |
| The agent mishears the values, registers and reads back the wrong ones, and the customer says yes anyway | The read-back exists for the customer to catch this; the gate cannot |
| A change said in words the check does not know ("yes, the week after though") | Condition 6 knows digits, weekdays, months and a list of words |
| The transcription differs from what was said | Voice only |

None of these lets the original failure through: one request after "just record it".

## Changes

| File | What |
| --- | --- |
| `builder/runtime/runtime.py` | `promise_problem`: the 22 checks of a promise, moved out of `pre_promise` unchanged, used by both hooks. `pre_check`, `post_check`. `unconfirmed` with its helpers. `pre_promise` calls the checks, then the gate |
| `builder/compiler/compiler.py` | `build_check` builds `check_promise` on the account operation. The block before a promise is rewritten: check, read back, record; the read-back cannot be waived. The sentence that read as if recording were an alternative to reading back is gone. The failure rules name the new refusal |
| `builder/validation/contract.py` | `RUNTIME_TOOLS`: the one named tool that is not an operation of the tool API. It is held to the URL and method of the account operation and to having a pre-hook; its outgoing request is checked against that operation |
| `builder/validation/spec.py` | A warning when the agent's language is not English |
| `builder/pipeline.py` | Passes the named tool to the contract check |
| `tests/freya_sim.py`, `benchmarks/harness/scenarios.py` | The stand-in engine gives hooks the turn counters and the conversation, as probed. A played `create_promise` takes the compliant path (check, read-back, yes, record) unless a test switches that off. The account reads of `check_promise` are logged apart from the bank's call log |
| `tests/test_m18_confirmation.py` | 45 tests |

Not changed: the spec format, the extraction prompt (digests `083c66052cdf131b` and `4a8a921557d57936`), the frozen packages, the references, the exposure log, any stored artifact, any deployed workflow.

The compiled workflow changes for every agent, so every version id changes. The three versions of M17 stay deployed and archived as they are.

| Spec | Size in M17 | Size now |
| --- | --- | --- |
| Brief A (`m17-a-v1`) | 244 KB | 286 KB |
| Brief A+B (`m17-b-v1`) | 270 KB | 313 KB |
| A+B with the memo (`m17-m16-v1`) | 277 KB | 320 KB |

The largest workflow Freya has accepted from this builder is 277 KB. **All three are now larger than anything that was accepted.** Whether the platform takes them is not known and is the first thing phase 5 has to find out.

## Tests

One full run of the final code:

| Run | Result |
| --- | --- |
| `python -m unittest discover -s tests` | 413 tests, no failure: 403 pass, 9 expected failures (the same 9 as before M16), 1 skipped. 368 existed before this phase, 45 are new |
| `python benchmarks/harness/run_benchmarks.py` | 124 scenarios: 108 pass, 0 fail, 5 known gaps, 11 not executable offline. The numbers of M17 |
| `python benchmarks/harness/packages.py verify` | Both manifests match. The exposure log has four entries |

No existing test fails. The checks of the development packages through the compiled workflow pass inside the suite.

What the 45 new tests play, each against the compiled workflow and the stand-in bank, with the ledger as the criterion:

- The request of the M17 call with nothing before it, and three ways an agent could try to obey the customer step by step.
- The allowed path; the check's request under all eight namings of the tool API; free wording of a read-back; seven ways of saying yes.
- No; yes with a change; an answer that is none; a yes taken back; a yes from before the check; a yes to another question; a flag from the model.
- Check and record in one turn and in one message, under both ways the engine was seen to run calls of one message and both arrival orders; the read-back said in the message of the record; two confirmed promises in one message; one read-back and one yes for several products.
- Amount one cent off, another date, another product, values written differently, new values replacing a check, a registration for another customer, a broken registration.
- A turn counter that cannot be read; a conversation that is missing or has one of seven other shapes; a conversation cut short; a fault at every point of the check's hook.
- A repeated check, a second record, a failing promise service, a failing account service, a stale check.
- Each limit and each of the four M16 rules refused by `check_promise` with the sentence `create_promise` gives; a confirmed promise under the M16 rules; the closed callback period.
- The tool, the contract check and what it still refuses, a bank that checks nothing, the untouched extraction, the language warning.

### Expectations that were changed

No expected bank record, no ledger assertion and no package key was changed. Five expectations name the tools or requests of a played call, and a played promise now has one more step:

| Where | Change |
| --- | --- |
| `tests/test_contract.py` | The requests of a played call: `check_promise` before `create_promise`, and an assertion that it leaves as the account request |
| `tests/test_m17.py` | The tools of the second part: `check_promise` added |
| `tests/test_block_safety.py` | The count of refusals in the runtime: 37 calls of `block` became 41 of `block` and `fail`. The helper takes the last request of a played call, since a check that passed may precede the refused record |
| `tests/test_hook_faults.py` | A promise is confirmed before a fault is put into its hook; the test of the behaviour before the fallback plays the bare request |
| `benchmarks/edge_cases/closure.yaml`, scenario EC-CL-07 | The tool list of a Brief A agent: `check_promise` added. The scenario's point, that there is no `add_note`, is unchanged |

Decisions in the test players that a reader should know:

- A played `create_promise` stands for what a compliant agent does to get there. Before M18 that was one call. Now the player checks, reads back and gets a yes first. Without this, every one of the earlier tests and scenarios would be refused at the gate and would test nothing.
- If the check refuses a value, the player does not go on to `create_promise`: a compliant agent would not. The existing tests then see the refusal of the check, which is the same sentence. That `create_promise` refuses the same values with the same sentence is tested separately, for each limit and each M16 rule.
- The account reads behind `check_promise` are kept in `bank.reads`, not in `bank.calls`. A scenario's list of bank calls therefore still lists what the agent's operations did. The reads are asserted in the new tests.

## What phase 5 has to verify live

In this order, because each decides whether the next is worth running:

1. **Size.** Does Freya accept and store a 286 to 320 KB workflow unchanged?
2. **The original failure.** `ADV-SKIP-CONFIRM` on the new Brief A version: no promise without read-back and yes.
3. **Does the phone model use the step?** In how many promise calls does it call `check_promise` before reading back, and in how many is it refused first and reads back twice?
4. **Regression.** Brief A (9 of 10 in M17) and Brief A+B (2 of 3), the same cases, the same judge. A check on "one read-back" or "create_promise called exactly once" is where the gate can cost a case.
5. **The M16 suite** on the memo version, including the closed-callback scenario.
6. **False refusals.** Every refusal of the gate in a call where the customer did confirm, with the reason it gave.
7. **Latency.** One more tool round trip per product before the read-back.
8. **The conversation.** That `system.conversation` has the probed form over a whole call, and that the gate never met a shape it did not know.
9. **The bank's ledger.** That the further `get_account` entries do not disturb the kit's scoring.

The gate should be kept only if 2 holds and 4 does not get worse; otherwise the M17 versions are there to go back to. That comparison and that decision need phases 3 and 4.

## Phase 3

Can begin. It depends on nothing in this phase except that the builder's commit must go into the build record: the same spec now compiles to another workflow than in M17, and only the commit tells the two apart.
