# Reference version 2 for the development packages C, D and E

The frozen packages in `benchmarks/packages/` are unchanged: their inputs, rules, checks and their own reference
(`key/reference.spec.json`, `key/reference.fit.json`, called v1 here) stay as frozen in `MANIFEST.json` there.
This directory holds a second reference spec and fit table per development package, written for the schema as it is
after M15 phase 3. The rules and the checks an extraction is judged by are the frozen ones in both versions; only the
hand-written spec that shows what the schema can carry, and the table that says so per rule, differ.

Why: v1 says of nine rules that agent-spec/1 cannot carry them. Since phase 3 it can, in whole or in part. Judged
against v1, an extraction that encodes such a rule is not judged on it at all, and one that leaves it out loses nothing.
Judged against v2, the rule counts like any other.

Packages F and G have no later reference and never get one: `packages.references` refuses it for their tiers.

Results are always reported with the reference version they were judged against. v1 and v2 numbers are not comparable.

Limits: both versions have the same author as the builder. The mutation lists of the packages address the v1 specs by
position and are run against v1 only.

## Package C

Spec: the content of `benchmarks/synthetic_briefs/brief-c.spec.yaml`.

| Rule | Fit in v1 | Fit in v2 | Carried by | Still open |
| --- | --- | --- | --- | --- |
| C-04 | partial | structured | `identity.questions[].normalize` = uppercase, remove_spaces | - |
| C-08 | unsupported | structured | `account.limits.promise_allowed` with a `by` value over `plan_active` and `days_late`; guidance `promise.not_allowed` | The block is enforced. The transfer to the arrears team that follows is an instruction to the phone model. |
| C-11 | unsupported | structured | `account.limits.earliest_date` = one calendar day after today | - |

Every difference between the two specs, by JSON pointer:

| Pointer | v1 | v2 |
| --- | --- | --- |
| `/account/limits/earliest_date` | (absent) | `earliest` |
| `/account/limits/promise_allowed` | (absent) | `eligible` |
| `/account/values/4/expr/kind` | (absent) | `calendar_days_after` |
| `/account/values/4/expr/n` | (absent) | `1` |
| `/account/values/4/label` | (absent) | `EARLIEST DATE that can be recorded` |
| `/account/values/4/name` | (absent) | `earliest` |
| `/account/values/5/expr/cases/0/expr/kind` | (absent) | `const` |
| `/account/values/5/expr/cases/0/expr/value` | (absent) | `False` |
| `/account/values/5/expr/cases/0/when/plan_active` | (absent) | `True` |
| `/account/values/5/expr/cases/1/expr/kind` | (absent) | `const` |
| `/account/values/5/expr/cases/1/expr/value` | (absent) | `False` |
| `/account/values/5/expr/cases/1/when/days_late/min` | (absent) | `61` |
| `/account/values/5/expr/cases/2/expr/kind` | (absent) | `const` |
| `/account/values/5/expr/cases/2/expr/value` | (absent) | `True` |
| `/account/values/5/expr/kind` | (absent) | `by` |
| `/account/values/5/label` | (absent) | `PROMISE ALLOWED` |
| `/account/values/5/name` | (absent) | `eligible` |
| `/guidance/promise.not_allowed` | (absent) | `Say that the arrears team will handle the account, call transfer_to_agent with queue arrears_team, then close_call transferred / arrears.` |
| `/identity/questions/0/normalize/0` | (absent) | `uppercase` |
| `/identity/questions/0/normalize/1` | (absent) | `remove_spaces` |

## Package D

Spec: the content of `benchmarks/synthetic_briefs/brief-d.spec.yaml`.

| Rule | Fit in v1 | Fit in v2 | Carried by | Still open |
| --- | --- | --- | --- | --- |
| D-06 | unsupported | structured | `promise.resolve_first` = secured products | Calls sent in one message fail closed; the order of speaking is conversational (check SD-12, live only). |
| D-11 | unsupported | partial | `promise.min_total` = 40 percent of the full amount | A guard only: a promise is refused once the total can no longer be reached. All-or-none is not enforceable, the tool API has no cancel (scenario SD-08b). |
| D-17 | unsupported | structured | close flag `note_recorded:dispute` at the top of `close.forced` | - |

Every difference between the two specs, by JSON pointer:

| Pointer | v1 | v2 |
| --- | --- | --- |
| `/close/forced/0/if` | `transferred` | `note_recorded:dispute` |
| `/close/forced/0/outcome` | `transferred` | `no_promise` |
| `/close/forced/0/reason` | (absent) | `dispute` |
| `/close/forced/0/reason_from` | `last_transfer_queue` | (absent) |
| `/close/forced/1/if` | `promise_recorded` | `transferred` |
| `/close/forced/1/outcome` | `promise` | `transferred` |
| `/close/forced/1/reason_from` | (absent) | `last_transfer_queue` |
| `/close/forced/2/if` | `identity_failed` | `promise_recorded` |
| `/close/forced/2/outcome` | `not_verified` | `promise` |
| `/close/forced/2/reason` | `failed` | (absent) |
| `/close/forced/3/if` | `callback_booked` | `identity_failed` |
| `/close/forced/3/outcome` | `not_reached` | `not_verified` |
| `/close/forced/3/reason` | `callback` | `failed` |
| `/close/forced/4/if` | `all_products_noted:paid` | `callback_booked` |
| `/close/forced/4/outcome` | `paid` | `not_reached` |
| `/close/forced/4/reason` | (absent) | `callback` |
| `/close/forced/5/if` | `any_note` | `all_products_noted:paid` |
| `/close/forced/5/outcome` | `no_promise` | `paid` |
| `/close/forced/5/reason_from` | `first_note_not:paid` | (absent) |
| `/close/forced/6/if` | (absent) | `any_note` |
| `/close/forced/6/outcome` | (absent) | `no_promise` |
| `/close/forced/6/reason_from` | (absent) | `first_note_not:paid` |
| `/promise/min_total/of` | (absent) | `full` |
| `/promise/min_total/percent` | (absent) | `40` |
| `/promise/resolve_first/0/secured` | (absent) | `True` |

## Package E

Spec: the content of `benchmarks/synthetic_briefs/brief-e.spec.yaml`.

| Rule | Fit in v1 | Fit in v2 | Carried by | Still open |
| --- | --- | --- | --- | --- |
| E-10 | unsupported | structured | `account.limits.earliest_date` with a `by_time` value: after 16:00 the next business day | The brief does not say whether 16:00 itself counts. |
| E-16 | partial | structured | `transfer.max_transfers` = 1 | Which of several reasons is acted on is conversational (check SE-13, live only). |
| E-18 | unsupported | partial | `promise.allowed_after_transfer_to` = plans | That the promise was confirmed before the transfer is conversational and cannot be checked in code. |

Every difference between the two specs, by JSON pointer:

| Pointer | v1 | v2 |
| --- | --- | --- |
| `/account/limits/earliest_date` | (absent) | `earliest` |
| `/account/values/4/expr/cases/0/expr/kind` | (absent) | `next_business_day` |
| `/account/values/4/expr/cases/0/when/after` | (absent) | `16:00` |
| `/account/values/4/expr/cases/1/expr/kind` | (absent) | `today` |
| `/account/values/4/expr/kind` | (absent) | `by_time` |
| `/account/values/4/label` | (absent) | `EARLIEST DATE that can be recorded` |
| `/account/values/4/name` | (absent) | `earliest` |
| `/promise/allowed_after_transfer_to/0` | (absent) | `plans` |
| `/transfer/max_transfers` | (absent) | `1` |
