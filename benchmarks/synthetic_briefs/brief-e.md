# Brief E: Meridian Energy, rules that replace other rules

**Synthetic.** This brief was written for the benchmark in this directory. It is not hackathon material, Meridian Energy does not exist, and every customer, debt and payment is invented. It is built like the official briefs so that a builder can be tried on a text in which later rules replace earlier ones, deadlines are unusual, and several reasons to hand a call over can apply at once.

Read the whole brief before acting on any rule. Five rules replace earlier ones (9, 12, 14, 18 and 20), and rule 8 decides against rule 7. Each of them says so.

## 1. The job

Meridian Energy calls customers who are late on an electricity account, a gas account, or both. The agent finds the customer, checks identity, states the arrears, takes a payment promise or hands the call over, and closes the call with the correct outcome.

## 2. Call variables

| Variable | Example | Meaning |
|---|---|---|
| `customer_id` | `c_5d03...` | Pass it to every tool call. |
| `first_name`, `last_name`, `title` | `Noor`, `Haddad`, `Ms` | Address the customer as title plus last name. |
| `now` | `2026-10-14T11:00` | The current date and time. All date rules use this. |

## 3. Opening and who is on the line

1. First sentence: "Hello, I am the digital assistant of Meridian Energy, and this call is recorded." Then ask "Am I speaking with {title} {last_name}?". Say nothing about any account before identity is verified.
2. If someone else answers, ask when the customer can be reached. A callback is possible only on the next business day, from 08:00 to 12:00, both included. Call `schedule_callback` and close as `not_reached` / `callback`. Otherwise close as `not_reached` / `no_callback`. If the number does not belong to the customer, close as `not_reached` / `wrong_number`.

## 4. Identity

3. Ask the date of birth, then the last four digits of the phone number on file, one at a time, with `verify_identity`. Three wrong answers in total end the check.

## 5. The accounts

4. After both questions pass, call `get_account`. It returns one or two products of type `electricity` or `gas`, each with `past_due`, `days_late` and `vulnerable`. Name them "electricity account" and "gas account" and state the past-due amount and the days late of each.

## 6. Dates

5. Business days are Sunday to Thursday. The company is closed on Friday and Saturday. Holidays: 2026-10-20, 2026-12-24.
6. A promise date is today or later, a business day, and not after the deadline.
7. The **deadline** is the last business day of the current month.
8. A promise date may never be more than ten calendar days after today; if the tenth day is not a business day, the last business day before it counts. Where rule 7 gives a later date than this, rule 8 wins.
9. **Replaces rules 7 and 8** when `days_late` is more than 30: the deadline is the next business day.
10. After 16:00 a promise for today cannot be recorded. The earliest date is then the next business day.

## 7. Amounts

11. The **floor** is 35.00 for an electricity account and 20 percent of the past-due amount for a gas account. The most that can be promised is the full past-due amount.
12. **Replaces rule 11** for an account with `vulnerable: true`: the floor is 10.00, whatever the product.
13. Always propose the full past-due amount first.
14. **Replaces rule 13** for an account with `vulnerable: true`: never propose the full amount. Start with the floor.

## 8. Recording

15. Read the account, the amount and the date back and call `create_promise` only after a clear yes. If it fails, try once more. If it fails again, say that you could not record it and close as `no_promise` / `service_error`.

## 9. Handing the call over

16. There are three reasons to hand a call over. If more than one applies, only the highest in this list is acted on, and exactly one transfer is made in a call:
    1. The household depends on medical equipment, or the customer asks for welfare help: queue `welfare`.
    2. The customer makes a formal complaint: queue `complaints`.
    3. The customer asks for a payment plan: queue `plans`.
17. After a transfer nothing more is recorded.
18. **Replaces rule 17** for the `plans` queue only: a promise that was already read back and confirmed before the transfer may still be recorded after it.

## 10. Closing

19. After a transfer close as `transferred` with the reason of the queue: `welfare`, `complaint` or `plan`.
20. **Replaces rule 19**: if the identity check failed, close as `not_verified` / `failed`, even when a transfer was made earlier in the call.
21. `add_note` is never used.
22. Every call ends with exactly one `close_call`. Where several rows could apply, the first one in this table that applies is used.

| Outcome | Reason | When |
|---|---|---|
| `not_verified` | `failed`, `declined` | rules 3, 20 |
| `transferred` | `welfare`, `complaint`, `plan` | rules 16, 19 |
| `promise` | none | a promise was recorded and no transfer was made |
| `paid` | none | the customer says the amount was already paid |
| `no_promise` | `refused`, `service_error` | the customer refuses every offer; rule 15 |
| `not_reached` | `callback`, `no_callback`, `wrong_number` | rule 2 |

## Tool API this brief assumes

The seven tools of the shared tool API, with these differences, written down in `brief-e.api.yaml`:

- A product has `type` (`electricity`, `gas`), `past_due`, `days_late` and `vulnerable`.
- `transfer_to_agent.queue` is `welfare`, `complaints` or `plans`.

## Scenarios

Defined in `brief-e.scenarios.yaml`; the expected bank records are in `../expected/ledger_assertions.yaml`.

| Id | What it tries | Expected at the bank |
|---|---|---|
| SE-01 | A promise for a Friday | refused; promise on Sunday 18 October |
| SE-02 | 14 October: end of month against ten days | deadline Thursday 22 October |
| SE-03 | 25 October: end of month against ten days | deadline Thursday 29 October |
| SE-04 | Forty-five days late, called on a Thursday | deadline Sunday 18 October |
| SE-05 | Electricity, and a vulnerable gas account | floors 35.00 and 10.00; two promises |
| SE-06 | A second transfer | refused; one transfer; `transferred` / `plan` |
| SE-07 | A confirmed promise after a transfer to `plans` | recorded; `transferred` / `plan` |
| SE-08 | A promise after a transfer to `welfare` | refused; `transferred` / `welfare` |
| SE-09 | A transfer, then a failed identity check | `not_verified` / `failed` |
| SE-10 | A promise for today at 16:30 | refused; promise on 15 October |
| SE-11 | A callback for today, and for tomorrow at 12:01 | both refused; booked for tomorrow 12:00 |
| SE-12 | 79.99, then 80.00 on a 400.00 gas account | one promise of 80.00 |
| SE-13 | A vulnerable household | first offer is the floor (conversation, not executable offline) |

## What agent-spec/1 can and cannot carry

| Rules | Fit |
|---|---|
| 1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 12, 15, 17, 19, 20, 21, 22 | Structured. The reference spec `brief-e.spec.yaml` carries them, including the three replacements 8, 9 and 12 and the order of rule 20, and the scenarios pass. |
| 13, 14 | Conversation rules. They are rule text; the extraction has to keep the replacement. |
| 16 | Partly. Queues and their reasons are structured, the priority is rule text, and nothing stops a second transfer. (SE-06) |
| 10 | Not expressible. There is no earliest date and no condition on the time of day. (SE-10) |
| 18 | Not expressible. "No promise after a transfer" is one switch for all queues. (SE-07) |

The reference spec does not list these rules under `unsupported`, because a spec with an `unsupported` entry is refused by validation and nothing could then be run. They are flagged here and in the scenarios instead.
