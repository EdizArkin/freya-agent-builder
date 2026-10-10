# Brief C: Northwind Credit Union, arrears on one product

**Synthetic.** This brief was written for the benchmark in this directory. It is not hackathon material, Northwind Credit Union does not exist, and every member, debt and payment is invented. It is built like the official briefs so that a builder can be tried on rules it has not seen: a different security check, and conditions on whether a promise may be taken at all.

## 1. The job

Northwind Credit Union calls members who are late on one product. The agent finds the member, checks identity, states the arrears, takes a payment promise where the account is eligible, and closes the call with the correct outcome.

## 2. Call variables

| Variable | Example | Meaning |
|---|---|---|
| `customer_id` | `c_91be...` | Pass it to every tool call. |
| `first_name`, `last_name` | `Imani`, `Brandt` | The person to ask for. Address the member by first name. |
| `now` | `2026-10-14T11:00` | The current date and time. All date rules use this. |

## 3. Opening and who is on the line

1. First sentence: "Hello, this is the automated assistant of Northwind Credit Union. This call may be monitored." Then ask "Am I speaking with {first_name} {last_name}?". Do not mention arrears, money or the product before identity is verified.
2. If someone else answers, say that you will call again and give no reason. Do not arrange a time: `schedule_callback` is never used in this brief. Close as `not_reached` / `no_callback`. If the person says the number does not belong to the member, close as `not_reached` / `wrong_number`.

## 4. Identity

3. Ask two security questions, one at a time and in this order: the postcode of the registered address, then the year of birth. Call `verify_identity` with field `postcode` or `birth_year`.
4. Send the postcode in capital letters without spaces ("nw1 4rt" is sent as `NW14RT`). Send the year of birth as four digits.
5. Two wrong answers in total end the check, whichever question they were on. The service itself allows three; this brief is stricter. Tell the member to visit a branch and close as `not_verified` / `failed`.
6. If the member declines to answer a security question, close as `not_verified` / `declined` at once. Do not explain and do not ask again.

## 5. The account

7. After both questions pass, call `get_account`. Name the product in words: "member loan" or "store card ending 6120". State the past-due amount and how many days late it is.
8. **Eligibility.** A promise may be recorded only when `plan_active` is `false` and `days_late` is 60 or less. Otherwise do not negotiate. Say that the arrears team will handle the account, call `transfer_to_agent` with queue `arrears_team` and close as `transferred` / `arrears`.

## 6. Dates

9. Business days are Monday to Saturday, except these holidays: 2026-10-19, 2026-11-02, 2026-12-25, 2026-12-26.
10. The **deadline** is the fifth calendar day after today when `days_late` is 1 to 30, and the second calendar day after today when it is more. If that day is not a business day, the deadline is the last business day before it.
11. A promise date is tomorrow or later, a business day, and not after the deadline. A promise for today is never recorded: payments take one day to set up.

## 7. Amounts

12. The **floor** is 40 percent of the past-due amount for a store card and 25 percent for a member loan. The most that can be promised is the full past-due amount. Any amount from the floor to the full amount, both included, is accepted.
13. Ask for the full amount on the deadline first. If the member refuses, make one persuasion attempt: the floor on the deadline. If that is refused too, close as `no_promise` / `refused`.

## 8. Recording

14. Read the amount and the date back and ask "Is that correct?". Call `create_promise` only after a clear yes. One promise per call.
15. If `create_promise` fails, do not try again. Say that you could not record it and close as `no_promise` / `service_error`.
16. After a recorded promise say: "Your promise is on file."

## 9. Other things members say

17. "I already paid": take no promise and close as `paid`.
18. A dispute about the debt, or a request for a person: call `transfer_to_agent` with queue `general` at once, without asking whether you can help first, and close as `transferred` / `general`. This applies before and after verification.
19. `add_note` is never used.

## 10. Closing

20. Every call ends with exactly one `close_call`.

| Outcome | Reason | When |
|---|---|---|
| `promise` | none | a promise was recorded |
| `paid` | none | rule 17 |
| `transferred` | `arrears`, `general` | rules 8, 18 |
| `no_promise` | `refused`, `service_error` | rules 13, 15 |
| `not_verified` | `failed`, `declined` | rules 5, 6 |
| `not_reached` | `no_callback`, `wrong_number` | rule 2 |

## Tool API this brief assumes

The seven tools of the shared tool API, with these differences, written down in `brief-c.api.yaml`:

- `verify_identity.field` is `postcode` or `birth_year`.
- `transfer_to_agent.queue` is `arrears_team` or `general`.
- A product has `type` (`member_loan`, `store_card`), `card_last4` for a store card, `past_due`, `days_late` and `plan_active`.
- `close_call.reason` is free text in the shared API, so the reasons of the table above need no change.

## Scenarios

Defined in `brief-c.scenarios.yaml`; the expected bank records are in `../expected/ledger_assertions.yaml`.

| Id | What it tries | Expected at the bank |
|---|---|---|
| SC-01 | A callback is offered | no callback tool exists; `not_reached` / `no_callback` |
| SC-02 | Both answers correct, in order | account loaded; product named "store card ending 6120" |
| SC-03 | Two wrong postcodes | `not_verified` / `failed`, account never loaded |
| SC-04 | Postcode passed as "nw1 4rt" | accepted as `NW14RT`, no wrong answer counted |
| SC-05 | 119.99, then 120.00 on a 300.00 store card | one promise of 120.00 |
| SC-06 | Sunday after a Saturday deadline | refused; promise on Saturday 17 October |
| SC-07 | A promise for today | refused; promise on 15 October |
| SC-08 | A promise on an account with an active plan | no promise; transfer to `arrears_team`; `transferred` / `arrears` |
| SC-09 | A second attempt after a failed `create_promise` | no promise; `no_promise` / `service_error` |
| SC-10 | Forty days late | deadline 16 October, floor 200.00; one promise |
| SC-11 | A person is asked for before verification | transfer to `general`; `transferred` / `general`; no security question |
| SC-12 | The first security question is declined | `not_verified` / `declined` (conversation, not executable offline) |

## What agent-spec/1 can and cannot carry

| Rules | Fit |
|---|---|
| 1, 2, 3, 5, 7, 9, 10, 12, 15, 16, 18, 19, 20 | Structured. The reference spec `brief-c.spec.yaml` carries them and the scenarios pass. |
| 6, 13, 14, 17 | Conversation rules. They are rule text in any brief. |
| 4 | Partly. Digits are normalised in code; a text answer is only trimmed, so "capitals, no spaces" depends on the model. (SC-04) |
| 8 | Not expressible. There is no condition for "no promise on this product". (SC-08) |
| 11 | Not expressible. There is a latest date per product and no earliest date. (SC-07) |

The reference spec does not list these three rules under `unsupported`, because a spec with an `unsupported` entry is refused by validation and nothing could then be run. They are flagged here and in the scenarios instead.
