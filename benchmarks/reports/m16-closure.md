# M16 closure: enforcement of rules that could only be reported

11 October 2026. Offline work only: no model call, no Freya deployment, no holdout run. The frozen packages, the references, the exposure log, the M15 reports and the extraction prompt are unchanged.

## The one thing to know first

M16 gives the schema, the validator and the runtime four new rule kinds and proves them with deterministic tests. **No extraction produces them yet.** The extraction prompt is the only place an extractor learns the schema, and it was not to be changed in M16. A build from a brief therefore behaves as before for these rules: the extractor reports them under `unsupported` and the compiled agent does not enforce them. The new enforcement reaches a deployed agent only through a spec that is edited by hand and built with `--spec`, or after the prompt is extended (see "Risks for M17").

## What the four holdout rules are

Taken from what the M15 extractions themselves reported as not expressible (saved in `outputs/experiments/holdout/`). No key, no brief and no holdout run was used.

| M15 risk | Kind of rule | New field |
| --- | --- | --- |
| F-19 | Amounts only in whole multiples of a step | `promise.amount_step`, optional `promise.amount_step_except_max` |
| F-18 | A promise for today only for the full amount; a lower amount needs a later date | `promise.min_amount_by_date`: a list of `{on_or_before: "today" or a date value, min_amount: an amount value}` |
| G-14 | All promises of one call on the same date | `promise.same_date` |
| G-03 | A period of the day in which no callback can be booked | `callback.closed`: a list of periods written with `from`, `after`, `until`, `before` |

The fields are general. No rule id, sentence, name or expected answer of a package is in production code. Whether these fields make the holdout checks of those four rules pass was not measured: that would be a new evaluation on F and G.

## State of each rule kind, by stage

(a) extracted, (b) representable in the schema, (c) compiled, (d) enforced deterministically offline, (e) verified on the live platform.

| Rule kind | (a) | (b) | (c) | (d) | (e) |
| --- | --- | --- | --- | --- | --- |
| Amount in steps | no: prompt not extended | yes | yes | yes | no |
| Lowest amount that depends on the date | no | yes | yes | yes | no |
| One date for all promises | no | yes | yes | in part, see below | no |
| Closed callback periods | no | yes | yes | yes | no |
| No promise when a limit cannot be worked out | not a spec field | n/a | yes | yes | no |
| A refusal told apart from a service failure | not a spec field | n/a | yes, as text | no: it is a sentence in the prompt | no |
| Read-back and a clear yes before a promise | rule text | no | prompt only | no | no |

## What is now enforced

Every item blocks the request before it leaves the pre-hook, by detaching it from the call as every other block does. The reason is put into the live state.

1. **Amount steps.** An amount that is not a whole multiple of the step is refused; the refusal names the nearest amounts inside the product's limits. Counted in cents, so 0.1 and 0.5 steps are exact. The full amount is an exception only where the spec says so. A step that reaches the runtime unreadable stops every promise.
2. **Lowest amount by date.** A promise dated on or before the given date needs at least the given amount; the day itself is included. Several such rules are all checked.
3. **One date for all promises.** A second promise must carry the date already recorded. The first promise of a call can go only to the first open product (in the order `promise.resolve_first` gives, then the list), so two first promises sent in one message cannot record two dates.
4. **Closed callback periods.** A time inside a closed period is refused on every day of the callback window. `from` and `until` include their minute, `after` and `before` do not: the same reading as `by_time`, now one function.
5. **Limits that cannot be worked out.** Before M16, a limit the spec names but the bank's data gave no value for was treated as no limit, so any amount or date was accepted for that product. Now such a product gets no promise. This applies to every spec, including Brief A and B.
6. **Build time.** Each field is validated; a spec with policy under `unsupported` still does not build without `--best-effort` (unchanged, now tested). The audit digest lists the four fields.

Also changed: after a refused call the live state says in a second line that the call was stopped by the policy check, is not a service failure and recorded nothing. That is an instruction to the phone model, not enforcement.

## What stays partial, prompt-only or unsupported

| Item | State |
| --- | --- |
| Extraction of the four fields | Not done. Prompt unchanged |
| One date for all promises | Partial. The first date cannot be changed afterwards (the tool API has no cancel). If a later product cannot take that date, it gets no promise; the model is told the latest date every product accepts before the first promise, as a fact |
| Order forced by the one-date rule | The first promise must be for the first open product. A brief that lets the customer start with another product needs a note on the earlier one first |
| Refusal channel | A blocked call still reaches the model as a failed tool call. The added line is text. A close with a service-failure reason after a refusal is not blocked: the spec does not say which reasons mean a service failure |
| Read-back before a promise or callback | Prompt only. The runtime sees tool calls, not what was said |
| Relative dates ("this Friday") | The runtime gives the model every nearby weekday as a date and refuses a date that is past, closed or outside the limits. A wrong weekday that lands on an allowed business day is not detectable |
| Time zones | The call's clock is taken as written; a zone suffix is ignored. Whether the platform's `now` is local time is not verified |
| `--best-effort` | Still builds an agent whose `unsupported` rules are in the rule text only |
| D-11 all-or-none, E-18 "confirmed before the transfer" | Unchanged from M15: partial |
| A pre-hook the platform does not run; shared-hook concurrency | Unchanged platform limits |

## Tests

| Run | Result |
| --- | --- |
| `python -m unittest discover -s tests` | 320 tests, OK, 9 expected failures (the same 9 as before M16) |
| `tests/test_enforcement_m16.py` (new) | 36 tests, OK |
| `tests/test_block_safety.py` | 33 block sites (27 before) under 8 tool APIs against 2 banks: 528 blocked calls, none changes a bank, each is detached, none carries a value of the call |
| `python benchmarks/harness/run_benchmarks.py` | 124 scenarios: 108 pass, 0 fail, 5 known gaps, 11 not executable offline (unchanged) |
| `python benchmarks/harness/packages.py verify` | package manifest and reference manifest match |

What the new tests cover:

- **Amount steps:** the multiple, one cent either side, amounts written as text or with extra decimals, cent steps, the full-amount exception, the other limits still applying, invalid steps at build time and at run time.
- **Lowest amount by date:** today and a later day, a named date with its boundary day, several rules together, what validation refuses.
- **One date:** the second promise, the first-product rule, two first promises in one message under three ways the engine may merge them and both arrival orders, a date the next product cannot take, the fact shown to the model.
- **Closed periods:** both readings of the limits at each minute, the next day, four ways of writing a time, several periods, what validation refuses.
- **Fail closed:** a product type the spec has no rule for; each missing limit.
- **Refusal channel:** nothing at the bank, the new line present, the service-failure advice absent, refusals not counted as failed attempts, a real failure still called a failure.
- **Edges of what was already enforced:** the day of a call at 23:59 and 00:00 and with zone suffixes, weekday names as dates across a weekend and a holiday, amount and date limits at their value and one step outside, rounding to cents, order with total with date rules together, a queue exception that opens nothing else.

Two of the 37 places where the runtime blocks have no entry in the block-safety table, because no valid spec with a listed product reaches them (limits that cannot be worked out; an unreadable step). The new test file plays both.

One defect was found and fixed while testing: the one-date rule and `promise.resolve_first` could each make the other's product wait. The order rule now follows `resolve_first`.

## Limits that need a live Freya run (M17)

1. Whether the phone model, after a refusal, reads the new line and offers the allowed value instead of reporting a technical problem.
2. Whether the model agrees one date for all products before the first promise when told so in the account facts.
3. Whether the larger hooks and prompts are accepted: the runtime source embedded in each hook grew.
4. Whether `now` on the platform is the bank's local time, which decides "today" and every time-of-day rule.
5. Brief A and B behaviour after the fail-closed change. Offline all 86 A/B scenarios pass; a live bank that sends a product without a field the spec reads would now get no promise for it where it got an unchecked one before.

## Risks for M17

1. **The extraction gap.** To make the four fields reachable, the schema part of the prompt has to describe them. Proposed wording, not applied:
   - `promise.amount_step`: "amounts are recorded only in whole multiples of this amount; `amount_step_except_max: true` if the full amount is always accepted".
   - `promise.min_amount_by_date`: "a promise dated on or before `on_or_before` (`\"today\"` or a date value) needs at least the amount value `min_amount`".
   - `promise.same_date`: "true if all promises of one call must carry one date".
   - `callback.closed`: "periods of the day in which no callback can be booked, each with `from`/`until` (included) or `after`/`before` (not included)".

   Changing the prompt ends the validity of the M15 numbers for it. C, D and E can show that nothing regressed. F and G have been seen, so a rerun on them would be post-exposure evidence, not a test.
2. **Fail-closed on missing limits** changes behaviour for every brief when bank data is incomplete.
3. **The one-date rule fixes the order of the first promise.**
4. **`--best-effort` in the final run** still deploys rules that are only reported.
5. **Nothing in M16 is verified live.**
