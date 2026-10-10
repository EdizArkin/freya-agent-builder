# Benchmark report

Run at 2026-10-10 22:14:51, offline. Nothing in this report measures what a phone model says or which tools it chooses; it reports what the generated runtime does with the tool calls each scenario plays.

Specs used: A = tests/fixture_spec.py (hand-written); B = tests/fixture_spec.py (hand-written); C = benchmarks/synthetic_briefs/brief-c.spec.yaml (hand-written reference); D = benchmarks/synthetic_briefs/brief-d.spec.yaml (hand-written reference); E = benchmarks/synthetic_briefs/brief-e.spec.yaml (hand-written reference)

| Result | Scenarios | Meaning |
| --- | --- | --- |
| PASS | 99 | every assertion held |
| FAIL | 0 | an assertion failed and no known gap explains it |
| GAP | 13 | failed where the definition documents a gap of the builder |
| UNEXPECTED PASS | 0 | has a documented gap but passed: the definition is out of date |
| NOT RUN | 11 | specification only, cannot be executed faithfully offline |

## By category

| Category | Brief | PASS | FAIL | GAP | NOT RUN |
| --- | --- | --- | --- | --- | --- |
| brief_c | C | 8 | 0 | 3 | 1 |
| brief_d | D | 8 | 0 | 3 | 1 |
| brief_e | E | 9 | 0 | 3 | 1 |
| closure | A | 7 | 0 | 0 | 0 |
| closure | B | 1 | 0 | 0 | 0 |
| conversations | A | 6 | 0 | 0 | 6 |
| dates | A | 10 | 0 | 0 | 1 |
| identity | A | 9 | 0 | 0 | 0 |
| identity | B | 1 | 0 | 0 | 0 |
| multi_product | B | 8 | 0 | 0 | 1 |
| notes | B | 10 | 0 | 0 | 0 |
| promises | A | 7 | 0 | 0 | 0 |
| retries | A | 8 | 0 | 2 | 0 |
| transfers | A | 5 | 0 | 2 | 0 |
| transfers | B | 2 | 0 | 0 | 0 |

## Scenarios

| Id | Tier | Result | Title |
| --- | --- | --- | --- |
| EC-CL-01 | runtime | PASS | An outcome without a reason loses a reason sent with it |
| EC-CL-02 | runtime | PASS | An outcome that needs a reason is not closed without one |
| EC-CL-03 | runtime | PASS | A reason the outcome table does not list is not sent |
| EC-CL-04 | runtime | PASS | A booked callback decides the close |
| EC-CL-05 | runtime | PASS | Nothing can be called after the close |
| EC-CL-06 | scripted | PASS | A wrong number ends with one call to the bank |
| EC-CL-07 | runtime | PASS | A Brief A agent has no add_note tool anywhere |
| EC-CL-08 | runtime | PASS | A promise outranks a paid note at the close |
| EC-CV-01 | scripted | PASS | Says yes, then no, then yes |
| EC-CV-02 | scripted | PASS | Changes the day at the read-back |
| EC-CV-03 | runtime | PASS | Presses for an exception to the deadline |
| EC-CV-04 | scripted | PASS | An unclear yes, then a clear one |
| EC-CV-05 | live_only | NOT RUN | Never gives a clear yes |
| EC-CV-06 | live_only | NOT RUN | Gives the security answers in two parts |
| EC-CV-07 | live_only | NOT RUN | Asks three times whether this is a scam or a robot |
| EC-CV-08 | runtime | PASS | Asks twice to change a recorded promise |
| EC-CV-09 | runtime | PASS | A relative presses for details and offers to pay |
| EC-CV-10 | live_only | NOT RUN | Mentions a job loss and still names a date |
| EC-CV-11 | live_only | NOT RUN | Says twice that she does not want to talk, before verification |
| EC-CV-12 | live_only | NOT RUN | Demands that half of the debt is cancelled |
| EC-DT-01 | runtime | PASS | Yesterday is refused, today is accepted |
| EC-DT-02 | runtime | PASS | A date after the deadline is refused and the deadline right after it is recorded |
| EC-DT-03 | runtime | PASS | Early band: the twelfth business day is the last one accepted |
| EC-DT-04 | runtime | PASS | A Saturday is refused and the next business day is named |
| EC-DT-05 | runtime | PASS | A bank holiday inside the deadline is refused |
| EC-DT-06 | runtime | PASS | A weekend day after the deadline is refused for the deadline, not for the weekend |
| EC-DT-07 | runtime | PASS | Dates that are not dates are refused |
| EC-DT-08 | scripted | PASS | The date of "this Friday" and "Friday next week" is given, not left to counting |
| EC-DT-09 | scripted | PASS | On a Friday, "next Friday" and the first offer are the Friday of next week |
| EC-DT-10 | runtime | PASS | Holidays at the turn of the year |
| EC-DT-11 | live_only | NOT RUN | A vague period and no exact day leads to the deadline |
| EC-ID-01 | runtime | PASS | Account tools do not exist before verification |
| EC-ID-02 | runtime | PASS | One correct answer of two is not verification |
| EC-ID-03 | runtime | PASS | Three wrong answers across both questions end the check for good |
| EC-ID-04 | runtime | PASS | Day and month the other way round is not a wrong answer |
| EC-ID-05 | runtime | PASS | Wrong answers are remembered after a correct one |
| EC-ID-06 | runtime | PASS | A failure of the identity service is not a wrong answer |
| EC-ID-07 | runtime | PASS | State handed in with the call cannot stand in for verification |
| EC-ID-08 | runtime | PASS | Numeric date and spaced digits are normalised before they are sent |
| EC-ID-09 | runtime | PASS | An answer the service cannot read is not counted |
| EC-ID-10 | scripted | PASS | Asks what the call is about instead of answering, twice |
| EC-MP-01 | runtime | PASS | One date for two products with different deadlines |
| EC-MP-02 | runtime | PASS | Each product has its own floor |
| EC-MP-03 | scripted | PASS | Three products on one date, one promise each |
| EC-MP-04 | runtime | PASS | Promise, refusal and dispute in one call: the promise wins the close |
| EC-MP-05 | runtime | PASS | What happens to one product does not leak into another |
| EC-MP-06 | runtime | PASS | The call is not closed while a product has neither a promise nor a note |
| EC-MP-07 | runtime | PASS | An unknown product id is refused when there are several products |
| EC-MP-08 | scripted | PASS | Refuses the product whose deadline is earlier than the date he named |
| EC-MP-09 | live_only | NOT RUN | One date for three products, after the deadline of two of them |
| EC-NT-01 | runtime | PASS | Every product refused: the close follows the notes |
| EC-NT-02 | runtime | PASS | A dispute on one product, a promise on the other |
| EC-NT-03 | runtime | PASS | Every product already paid closes as paid |
| EC-NT-04 | runtime | PASS | Paid and refused: the reason is the one that is not paid |
| EC-NT-05 | runtime | PASS | The closing reason follows the order of the account, not the order of the notes |
| EC-NT-06 | runtime | PASS | A promise that fails twice ends as a service_error note |
| EC-NT-07 | runtime | PASS | One note per product, and none for a promised product |
| EC-NT-08 | scripted | PASS | Hardship with the support team declined |
| EC-NT-09 | runtime | PASS | A failed note is repeated and saved once |
| EC-NT-10 | runtime | PASS | A note with a reason outside the list is refused |
| EC-PR-01 | runtime | PASS | One cent below the floor is refused, the floor itself is accepted |
| EC-PR-02 | runtime | PASS | One cent above the past-due amount is refused |
| EC-PR-03 | runtime | PASS | Zero, negative and non-numeric amounts are refused |
| EC-PR-04 | runtime | PASS | A second promise for the same product is refused |
| EC-PR-05 | runtime | PASS | Amount as text and date with a time are normalised |
| EC-PR-06 | runtime | PASS | No promise without a loaded account |
| EC-PR-07 | runtime | PASS | An invented product id on a one-product account is corrected |
| EC-RT-01 | runtime | PASS | HTTP 500 and then success |
| EC-RT-02 | runtime | PASS | HTTP 500 twice |
| EC-RT-03 | runtime | PASS | A third attempt after the retry is used up never reaches the ledger |
| EC-RT-04 | runtime | GAP | A slow promise is overtaken by the close sent with it |
| EC-RT-05 | runtime | PASS | The mark of a failed call does not hold back a close one turn later |
| EC-RT-06 | runtime | PASS | A close right after a failed call is not held back |
| EC-RT-07 | runtime | PASS | After a failed transfer a different, valid call goes through |
| EC-RT-08 | runtime | PASS | A failed close leaves the call open |
| EC-RT-09 | runtime | PASS | A failed callback booking is repeated and booked once |
| EC-RT-10 | runtime | GAP | The same promise sent twice in one message is recorded once |
| EC-TR-01 | runtime | PASS | A transfer before verification closes as transferred |
| EC-TR-02 | runtime | PASS | After a transfer the close cannot carry another outcome |
| EC-TR-03 | runtime | GAP | Transfer and close in one message, the close arriving first |
| EC-TR-04 | runtime | PASS | A failed transfer is repeated and happens once |
| EC-TR-05 | runtime | PASS | A transfer that never works does not force the outcome |
| EC-TR-06 | runtime | PASS | No note after a transfer |
| EC-TR-07 | runtime | PASS | A promise agreed but not yet recorded is not recorded after the transfer |
| EC-TR-08 | runtime | PASS | A transfer after a recorded promise closes as transferred |
| EC-TR-09 | runtime | GAP | Callback and close in one message, the close arriving first |
| SC-01 | runtime | PASS | There is no callback tool in this agent |
| SC-02 | runtime | PASS | Postcode first, then year of birth |
| SC-03 | runtime | PASS | Two wrong answers end the check |
| SC-04 | runtime | GAP | The postcode is sent in capitals without spaces |
| SC-05 | runtime | PASS | Floor as a percentage of the past-due amount |
| SC-06 | runtime | PASS | Saturday is a business day and the deadline rolls back over a holiday |
| SC-07 | runtime | GAP | A promise for today is refused |
| SC-08 | runtime | GAP | No promise on an account that is already on a plan |
| SC-09 | runtime | PASS | No retry after a failed promise |
| SC-10 | runtime | PASS | More than thirty days late: two calendar days, a quarter as the floor |
| SC-11 | scripted | PASS | A request for a person before verification |
| SC-12 | live_only | NOT RUN | Declines the first security question |
| SD-01 | runtime | PASS | A mortgage is promised in full or not at all |
| SD-02 | runtime | PASS | Three products, three different deadlines |
| SD-03 | runtime | PASS | Notes are added after a transfer |
| SD-04 | runtime | PASS | No promise after a transfer, but the notes still go in |
| SD-05 | runtime | PASS | Two failures and a third attempt that works |
| SD-06 | runtime | PASS | Three failures use up the attempts |
| SD-07 | runtime | GAP | No promise on the card while the mortgage is open |
| SD-08 | runtime | GAP | Promises below the household minimum are not recorded |
| SD-09 | runtime | GAP | A dispute outranks a promise at the close |
| SD-10 | runtime | PASS | Paid and deferred: the close takes the reason that is not paid |
| SD-11 | runtime | PASS | A callback is never for today |
| SD-12 | live_only | NOT RUN | Every product is stated, the secured one first |
| SE-01 | runtime | PASS | Friday is not a business day, Sunday is |
| SE-02 | runtime | PASS | Two deadline rules: the ten-day rule wins mid-month |
| SE-03 | runtime | PASS | Two deadline rules: the end of the month wins late in the month |
| SE-04 | runtime | PASS | More than thirty days late: the next business day, across the weekend |
| SE-05 | runtime | PASS | The vulnerable floor replaces the product floor |
| SE-06 | runtime | GAP | A second transfer is refused |
| SE-07 | runtime | GAP | A confirmed promise may still be recorded after a transfer to plans |
| SE-08 | runtime | PASS | Nothing is recorded after a transfer to welfare |
| SE-09 | runtime | PASS | A failed identity check outranks an earlier transfer |
| SE-10 | runtime | GAP | After 16:00 a promise for today is refused |
| SE-11 | runtime | PASS | A callback only on the next business day before noon |
| SE-12 | runtime | PASS | The gas floor is a fifth of the past-due amount |
| SE-13 | live_only | NOT RUN | A vulnerable household is offered the floor first, never the full amount |

## Known gaps confirmed by this run

| Kind | Id | Title |
| --- | --- | --- |
| agent-spec/1 cannot express the rule | SC-04 | The postcode is sent in capitals without spaces |
| agent-spec/1 cannot express the rule | SC-07 | A promise for today is refused |
| agent-spec/1 cannot express the rule | SC-08 | No promise on an account that is already on a plan |
| agent-spec/1 cannot express the rule | SD-07 | No promise on the card while the mortgage is open |
| agent-spec/1 cannot express the rule | SD-08 | Promises below the household minimum are not recorded |
| agent-spec/1 cannot express the rule | SD-09 | A dispute outranks a promise at the close |
| agent-spec/1 cannot express the rule | SE-06 | A second transfer is refused |
| agent-spec/1 cannot express the rule | SE-07 | A confirmed promise may still be recorded after a transfer to plans |
| agent-spec/1 cannot express the rule | SE-10 | After 16:00 a promise for today is refused |
| cannot be enforced from inside tool hooks on this platform | EC-RT-04 | A slow promise is overtaken by the close sent with it |
| cannot be enforced from inside tool hooks on this platform | EC-RT-10 | The same promise sent twice in one message is recorded once |
| cannot be enforced from inside tool hooks on this platform | EC-TR-03 | Transfer and close in one message, the close arriving first |
| cannot be enforced from inside tool hooks on this platform | EC-TR-09 | Callback and close in one message, the close arriving first |

## Failed assertions

### EC-RT-04 — A slow promise is overtaken by the close sent with it (GAP)
Documented gap (cannot be enforced from inside tool hooks on this platform): The hooks of the close cannot see another call of the same message: each call starts from its own copy of the variables. When the close reaches the bank first, the other call is refused by the bank. Nothing inside a hook can prevent that.
- step 3 (create_promise, sent together): expected ok, got bank_refused
- step 3 (create_promise, sent together): tool result lacks 'is now recorded' (it says: NOT RECORDED: the service failed and nothing was recorded for credit card ending 7312. Do NOT tell the customer it is recorded. Call create_promise again now with exactly the same product, amount and date.)
- step 3 (close_call, sent together): expected detached, got ok
- step 4 (check): the call is in end, expected negotiate
- step 5 (close_call): expected ok, got unavailable
- ledger promises is [], expected [('p1', 480.0, '2026-10-16')]
- bank calls were ['verify_identity', 'verify_identity', 'get_account', 'close_call', 'create_promise!refused'], expected ['verify_identity', 'verify_identity', 'get_account', 'create_promise', 'close_call']
- the last call at the bank is create_promise!refused, expected close_call
- bank calls: verify_identity > verify_identity > get_account > close_call > create_promise!refused

### EC-RT-10 — The same promise sent twice in one message is recorded once (GAP)
Documented gap (cannot be enforced from inside tool hooks on this platform): Both calls start from the same copy of the variables, so neither can know of the other, and the tool API has no idempotency key. Whether the bank itself refuses a second promise for a product is not known; the offline bank records both.
- step 3 (create_promise, sent together): expected blocked, got ok
- ledger promises is [('p1', 480.0, '2026-10-16'), ('p1', 480.0, '2026-10-16')], expected [('p1', 480.0, '2026-10-16')]
- bank calls: verify_identity > verify_identity > get_account > create_promise > create_promise > close_call

### EC-TR-03 — Transfer and close in one message, the close arriving first (GAP)
Documented gap (cannot be enforced from inside tool hooks on this platform): The hooks of the close cannot see another call of the same message: each call starts from its own copy of the variables. When the close reaches the bank first, the other call is refused by the bank. Nothing inside a hook can prevent that.
- step 3 (transfer_to_agent, sent together): expected ok, got bank_refused
- step 3 (transfer_to_agent, sent together): tool result lacks 'is done' (it says: The transfer did NOT work. Call transfer_to_agent once more with the same queue.)
- step 3 (close_call, sent together): expected detached, got ok
- step 4 (check): the call is in end, expected negotiate
- step 5 (close_call): expected ok, got unavailable
- ledger transfers is [], expected ['restructuring']
- bank calls were ['verify_identity', 'verify_identity', 'get_account', 'close_call', 'transfer_to_agent!refused'], expected ['verify_identity', 'verify_identity', 'get_account', 'transfer_to_agent', 'close_call']
- the last call at the bank is transfer_to_agent!refused, expected close_call
- bank calls: verify_identity > verify_identity > get_account > close_call > transfer_to_agent!refused

### EC-TR-09 — Callback and close in one message, the close arriving first (GAP)
Documented gap (cannot be enforced from inside tool hooks on this platform): The hooks of the close cannot see another call of the same message: each call starts from its own copy of the variables. When the close reaches the bank first, the other call is refused by the bank. Nothing inside a hook can prevent that.
- step 1 (schedule_callback, sent together): expected ok, got bank_refused
- step 1 (schedule_callback, sent together): tool result lacks 'The callback is booked' (it says: The callback was NOT booked. Call schedule_callback once more with the same values.)
- step 1 (close_call, sent together): expected detached, got ok
- step 2 (check): the call is in end, expected contact
- step 3 (close_call): expected ok, got unavailable
- ledger callbacks is [], expected [('2026-10-15', '11:00')]
- bank calls were ['close_call', 'schedule_callback!refused'], expected ['schedule_callback', 'close_call']
- the last call at the bank is schedule_callback!refused, expected close_call
- bank calls: close_call > schedule_callback!refused

### SC-04 — The postcode is sent in capitals without spaces (GAP)
Documented gap (agent-spec/1 cannot express the rule): Answers of format text are only trimmed. The schema has no way to say "upper case, no spaces", so the normalisation depends on the model.
- step 1 (verify_identity): tool result lacks 'That answer is correct' (it says: That answer is WRONG. Wrong answers left before the check ends: 1. Do not move to another question. Ask the same question again: "the postcode of your registered address" (field postcode).)
- step 3 (check): the call is in contact, expected negotiate
- step 3 (check): state wrong is 1, expected 0
- bank calls: verify_identity > verify_identity > close_call

### SC-07 — A promise for today is refused (GAP)
Documented gap (agent-spec/1 cannot express the rule): The schema has a latest date per product but no earliest date. "Tomorrow or later" lives in the rule text only.
- step 3 (create_promise): expected refused, got ok
- step 4 (create_promise): expected ok, got refused
- ledger promises is [('p1', 300.0, '2026-10-14')], expected [('p1', 300.0, '2026-10-15')]
- bank calls: verify_identity > verify_identity > get_account > create_promise > create_promise!refused > close_call

### SC-08 — No promise on an account that is already on a plan (GAP)
Documented gap (agent-spec/1 cannot express the rule): The schema cannot say that a product is not eligible for a promise. Limits are an amount range and a latest date; a condition on other fields is rule text only.
- step 3 (create_promise): expected blocked, got ok
- ledger promises is [('p1', 500.0, '2026-10-16')], expected []
- bank calls: verify_identity > verify_identity > get_account > create_promise > transfer_to_agent > close_call

### SD-07 — No promise on the card while the mortgage is open (GAP)
Documented gap (agent-spec/1 cannot express the rule): The schema has no rule that makes a promise on one product depend on the state of another. Product order is rule text only.
- step 3 (create_promise): expected blocked, got ok
- step 5 (create_promise): expected ok, got refused
- bank calls: verify_identity > verify_identity > get_account > create_promise > create_promise > create_promise!refused > close_call

### SD-08 — Promises below the household minimum are not recorded (GAP)
Documented gap (agent-spec/1 cannot express the rule): Limits are per product. The schema has no limit across products, so a minimum on the sum of all promises is rule text only.
- step 4 (create_promise): expected blocked, got ok
- step 5 (add_note): expected ok, got refused
- ledger outcome is 'promise', expected 'no_promise'
- ledger reason is None, expected 'refused'
- ledger promises is [('p2', 180.0, '2026-10-16')], expected []
- ledger notes is [('p1', 'refused')], expected [('p1', 'refused'), ('p2', 'refused')]
- bank calls: verify_identity > verify_identity > get_account > add_note > create_promise > add_note!refused > close_call

### SD-09 — A dispute outranks a promise at the close (GAP)
Documented gap (agent-spec/1 cannot express the rule): close.forced can test "a promise was recorded" and "notes without any promise", but not "some product has a note with this reason". The promise rule fires first.
- ledger outcome is 'promise', expected 'no_promise'
- ledger reason is None, expected 'dispute'
- bank calls: verify_identity > verify_identity > get_account > create_promise > add_note > close_call

### SE-06 — A second transfer is refused (GAP)
Documented gap (agent-spec/1 cannot express the rule): Nothing limits the number of transfers. A second transfer is carried out and its queue then decides the closing reason.
- step 4 (transfer_to_agent): expected blocked, got ok
- ledger reason is 'complaint', expected 'plan'
- ledger transfers is ['plans', 'complaints'], expected ['plans']
- bank calls: verify_identity > verify_identity > get_account > transfer_to_agent > transfer_to_agent > close_call

### SE-07 — A confirmed promise may still be recorded after a transfer to plans (GAP)
Documented gap (agent-spec/1 cannot express the rule): promise.forbidden_after_transfer is one switch for every queue. An exception for one queue cannot be written, and the reference spec keeps the general rule.
- step 4 (create_promise): expected ok, got refused
- ledger promises is [], expected [('p1', 180.0, '2026-10-15')]
- bank calls: verify_identity > verify_identity > get_account > transfer_to_agent > create_promise!refused > close_call

### SE-10 — After 16:00 a promise for today is refused (GAP)
Documented gap (agent-spec/1 cannot express the rule): No limit depends on the time of day, and there is no earliest date. The 16:00 rule is rule text only.
- step 3 (create_promise): expected refused, got ok
- step 4 (create_promise): expected ok, got refused
- ledger promises is [('p1', 180.0, '2026-10-14')], expected [('p1', 180.0, '2026-10-15')]
- bank calls: verify_identity > verify_identity > get_account > create_promise > create_promise!refused > close_call

