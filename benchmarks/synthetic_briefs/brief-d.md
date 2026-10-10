# Brief D: Harbor Lending, households with several accounts

**Synthetic.** This brief was written for the benchmark in this directory. It is not hackathon material, Harbor Lending does not exist, and every customer, debt and payment is invented. It is built like the official briefs so that a builder can be tried on rules that tie several products together and on notes that depend on what happened in the call.

## 1. The job

Harbor Lending calls customers who are late on one to four products. The agent finds the customer, checks identity, states every product, takes promises in a fixed order, notes every product that ends without a promise, and closes the call with the correct outcome.

## 2. Call variables

| Variable | Example | Meaning |
|---|---|---|
| `customer_id` | `c_27aa...` | Pass it to every tool call. |
| `first_name`, `last_name`, `title` | `Erik`, `Lindqvist`, `Mr` | Address the customer as title plus last name. |
| `now` | `2026-10-14T11:00` | The current date and time. All date rules use this. |

## 3. Opening and who is on the line

1. First sentence: "Good day, this is the virtual assistant of Harbor Lending, and this call is recorded." Then ask "Am I speaking with {title} {last_name}?". Say nothing about any account before identity is verified.
2. If someone else answers, ask when the customer can be reached. A callback is possible only on one of the next two business days, never today, from 09:00 to 17:00, both included. Call `schedule_callback` and close as `not_reached` / `callback`. If the time offered is outside that window, ask once for another; if that is outside too, close as `not_reached` / `no_callback`. If the number does not belong to the customer, close as `not_reached` / `wrong_number`.

## 4. Identity

3. Ask the date of birth, then the last four digits of the phone number on file, one at a time, with `verify_identity`. Three wrong answers in total end the check: close as `not_verified` / `failed`. A date with day and month the other way round comes back with `hint: "day_month_swapped"` and does not count. A customer who declines twice is closed as `not_verified` / `declined`.

## 5. The products

4. After both questions pass, call `get_account`. It returns one to four products of type `mortgage`, `credit_card` or `overdraft`. A mortgage has `secured: true`. Name them "mortgage", "credit card ending 4417" and "overdraft".
5. State every product, one sentence each, with its past-due amount and days late, before asking for anything.
6. **Order.** Deal with secured products first, then with the others in the order `get_account` returned them. No promise may be recorded on an unsecured product while a secured product has neither a promise nor a note.

## 6. Amounts and dates

7. The **floor**: a mortgage can only be promised in full; for a credit card the floor is 30 percent of the past-due amount; for an overdraft 50 percent. The most that can be promised is the full past-due amount.
8. Business days are Monday to Friday, except these holidays: 2026-11-05, 2026-12-25, 2027-01-01.
9. The **deadline**: for a mortgage the third business day after today. For the other products the tenth business day after today when `days_late` is 1 to 14, and the fifth when it is 15 or more.
10. A promise date is today or later, a business day, and not after that product's deadline.
11. **Household minimum.** The promises recorded in one call must add up to at least 40 percent of the total past due across all products. Work the total out before recording anything. If it is lower, say so and ask the customer to raise an amount. If the customer does not, record none of the promises and note every product `refused`.

## 7. Recording

12. Read all promises back together and ask for one confirmation. Then call `create_promise` once per product.
13. If `create_promise` fails for a product, try again, up to three attempts in total for that product. After the third failure call `add_note` with reason `service_error` for it and keep the other promises.

## 8. Notes

14. Every product that ends without a promise gets `add_note` with one of these reasons: `refused`, `dispute`, `paid`, `deferred`, `service_error`. `deferred` means the customer asked for a date after the deadline and did not accept the deadline.
15. A request for a new payment plan: describe no plan and call `transfer_to_agent` with queue `restructuring`. No promise may be recorded after the transfer. Before closing, call `add_note` with reason `deferred` for every product without a promise. Notes are added after a transfer in this brief.
16. A request for a human: ask once whether you can help first. If the customer insists, call `transfer_to_agent` with queue `general`, note the open products as in rule 15, and close as `transferred` / `human`.

## 9. Closing

17. **A dispute comes first.** If any product is noted `dispute`, close as `no_promise` / `dispute`, even when promises were recorded on other products. The promises stay.
18. Otherwise: after a transfer close as `transferred` with that transfer's reason. With at least one promise close as `promise`. With no promise: if every product is noted `paid` close as `paid`; otherwise close as `no_promise` with the reason of the first product, in `get_account` order, whose note is not `paid`.
19. Every call ends with exactly one `close_call`.

| Outcome | Reason | When |
|---|---|---|
| `promise` | none | rule 18 |
| `paid` | none | rule 18 |
| `transferred` | `restructuring`, `human` | rules 15, 16 |
| `no_promise` | `refused`, `dispute`, `deferred`, `service_error` | rules 17, 18 |
| `not_verified` | `failed`, `declined` | rule 3 |
| `not_reached` | `callback`, `no_callback`, `wrong_number` | rule 2 |

## Tool API this brief assumes

The seven tools of the shared tool API, with these differences, written down in `brief-d.api.yaml`:

- A product has `type` (`mortgage`, `credit_card`, `overdraft`), `card_last4` for a credit card, `past_due`, `days_late` and `secured`.
- `add_note.reason` is `refused`, `dispute`, `paid`, `deferred` or `service_error`.

## Scenarios

Defined in `brief-d.scenarios.yaml`; the expected bank records are in `../expected/ledger_assertions.yaml`.

| Id | What it tries | Expected at the bank |
|---|---|---|
| SD-01 | Half of the mortgage arrears | refused; one promise for the full amount |
| SD-02 | Mortgage, card 16 days late, overdraft 5 days late | deadlines 19, 21 and 28 October; three promises |
| SD-03 | A plan request before any promise | transfer; two `deferred` notes; `transferred` / `restructuring` |
| SD-04 | A promise on the card after the transfer | refused; mortgage promise stays; card noted `deferred` |
| SD-05 | Two service failures, third attempt | one promise |
| SD-06 | Three service failures, fourth attempt | no promise; `service_error` note; `no_promise` / `service_error` |
| SD-07 | The card is recorded before the mortgage | refused until the mortgage has a promise |
| SD-08 | Mortgage refused, card floor only | no promise; two `refused` notes; `no_promise` / `refused` |
| SD-09 | Mortgage promised, card disputed | promise stays; `no_promise` / `dispute` |
| SD-10 | Mortgage paid, card deferred | `no_promise` / `deferred` |
| SD-11 | A callback for today, then for the day after tomorrow | first refused; booked for 16 October 17:00 |
| SD-12 | Three products, everything accepted | three promises (conversation, not executable offline) |

## What agent-spec/1 can and cannot carry

| Rules | Fit |
|---|---|
| 1, 2, 3, 4, 7, 8, 9, 10, 13, 15, 16, 18, 19 | Structured. The reference spec `brief-d.spec.yaml` carries them and the scenarios pass. |
| 5, 12 | Conversation rules. They are rule text in any brief. |
| 14 | Partly. The notes and their effect on the close are structured; that every open product gets one is left to the model. |
| 6 | Not expressible. Nothing makes a promise on one product depend on the state of another. (SD-07) |
| 11 | Not expressible. Limits are per product; there is none across products. (SD-08) |
| 17 | Not expressible. The close can test "a promise was recorded" or "notes and no promise", not "some note has this reason". (SD-09) |

The reference spec does not list these rules under `unsupported`, because a spec with an `unsupported` entry is refused by validation and nothing could then be run. They are flagged here and in the scenarios instead.
