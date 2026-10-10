# Requirements and coverage

One row per rule of the source documents. The official briefs are in the starter kit and are not copied here; the rule numbers are theirs.
Rows P-01 to P-05 are not rules of a brief. They are behaviours of the Freya engine and the bank seen in live calls on 2026-10-10 that every generated agent has to survive.

How to read the columns:

- **Enforced by** says who makes the rule hold. `runtime` is generated code that runs whatever the phone model does. `graph` is the shape of the workflow: which tools exist in which part of the call. `model` means the rule is text in the prompt and holds only as far as the phone model follows it.
- **Existing tests** are the offline tests in `tests/` and the live practice cases (`PUB-…`) that touched the rule before this benchmark existed.
- **Benchmark** lists the scenarios of this suite. `*` marks a scenario that documents a known gap of the builder. `°` marks a scenario that is a specification only and is not executed offline.
- **Missing** says what is still not covered, or covered only by a live run.

## Brief A (official, one product)

Source: `briefs/brief-a.md` of the starter kit, rules 1 to 47.

| Rule | Required behaviour | Preconditions | Tools (allowed; forbidden) | State change | Edge cases | Enforced by | Existing tests | Benchmark | Missing |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A-01 | First sentence: digital assistant of Freya Bank, call is recorded. No "collections", "debt", "payment" or product before verification, to anyone. | call starts | none | none | relatives; a customer who asks why | graph (opening line is fixed speech); model (forbidden words) | test_compiler Workflow; live PUB-02 judge | EC-ID-10, EC-CV-09 | forbidden words are checked live only |
| A-02 | Ask "Am I speaking with {title} {last_name}?". | call starts | none | none | — | graph | live, every public case | — | no benchmark scenario |
| A-03 | Only "yes", "that's me", "speaking" or the own name confirm. Otherwise ask once more; still unclear: close not_reached / unclear. A question is answered first. | before verification | close_call; forbidden: verify_identity before a confirmation | closed | "hello", "go ahead", "who is this" | model | none | EC-CV-07° | no scenario with an unclear answer; live only |
| A-04 | Asked whether it is a robot or an AI: say it is Freya Bank's digital assistant, continue. | any time | none | none | asked repeatedly | model | live PUB-04 judge | EC-CV-07° | — |
| A-05 | Someone else answers: do not say why you call; ask when the customer can be reached. | third party | forbidden: verify_identity, get_account | none | the person offers to pay | model; graph (no account tool exists) | test_enforcement Authorization; live PUB-02 | EC-CV-09 | — |
| A-06 | Callback today or the next business day, 10:00 to 19:00 inclusive, later than now. schedule_callback, then close not_reached / callback. | third party gave a time | schedule_callback, close_call | callback booked; close forced to not_reached / callback | 10:00, 19:00, 19:01; now after the window; second booking | runtime (pre_callback, pre_close) | test_enforcement Blocking; test_runtime Calendar | EC-RT-09, EC-TR-09, EC-CL-04, EC-CV-09 | — |
| A-07 | A time outside the window: ask once for another. Still outside: no booking, close not_reached / no_callback. | third party | forbidden: schedule_callback outside the window | none | — | runtime (block); model (asking once) | test_enforcement Blocking | — | counting "once" is live only |
| A-08 | The number does not belong to the customer: close not_reached / wrong_number. | third party | close_call only | closed | — | model | none | EC-CL-06 | — |
| A-09 | The customer has died: "I am sorry for your loss.", ask nothing, close not_reached / deceased. | third party | close_call only | closed | — | model | none | — | no scenario; live only |
| A-10 | A relative may not answer security questions or learn anything about the account, even when offering to pay. | third party | forbidden: verify_identity, get_account, create_promise | none | relative knows the date of birth | graph (account tools); model (verify_identity) | test_enforcement Authorization | EC-ID-01, EC-CV-09 | the runtime cannot know who answers a security question |
| A-11 | Two questions, one at a time: date of birth, then last four phone digits. Date as YYYY-MM-DD; numbers only means day, month, year. | customer confirmed | verify_identity | verified fields | numeric dates, two-digit years, spaced digits, half answers | runtime (pre_identity normalises); model (order, completeness) | test_enforcement Persistence | EC-ID-02, EC-ID-08, EC-ID-09, EC-CV-06° | — |
| A-12 | A wrong answer may be asked again. Three wrong answers in total end the check, whichever question. | identity pending | verify_identity | wrong count | wrong answers on both questions; service failure; unreadable answer | runtime (post_identity) and the bank | test_enforcement Authorization, Failures; test_runtime Calls | EC-ID-03, EC-ID-05, EC-ID-06, EC-ID-09, EC-CV-06° | — |
| A-13 | Hint day_month_swapped: ask for the month as a word; not a wrong answer. | identity pending | verify_identity | none | — | runtime | test_runtime Calls | EC-ID-04 | — |
| A-14 | After three wrong answers: do not state the debt, point to app or branch, close not_verified / failed. | identity failed | close_call; forbidden: get_account, create_promise | identity failed for good; close forced | a correct answer after the failure; model closes otherwise | graph; runtime (pre_close) | test_enforcement Authorization; live PUB-03 | EC-ID-03 | — |
| A-15 | Customer does not want to answer, or asks what the call is about: explain once; second time close not_verified / declined. | identity pending | close_call | closed | — | model | none | EC-ID-10, EC-CV-11° | — |
| A-16 | After both questions pass, call get_account. | both answers verified | get_account (run by the engine) | account loaded | one answer only; lookup fails; state handed in with the call | graph | test_enforcement Authorization, Failures, Persistence | EC-ID-01, EC-ID-02, EC-ID-07, EC-PR-06 | — |
| A-17 | Say collections team, late payment; name the product in words; state amount and days late. | account loaded | none | none | — | model with facts from runtime | live PUB-01 judge | — | live only |
| A-18 | Deadline: days late 1 to 7 is the 12th business day after today with 1 persuasion attempt; 8 to 45 the 7th with 2. | account loaded | none | limits per product | days 7 and 8; holidays inside the span; turn of the year | runtime (account_facts) | test_runtime Calendar | EC-DT-02, EC-DT-03, EC-DT-09, EC-DT-10 | days late 0 or above 45 is not defined by the brief |
| A-19 | A promise date is today or later, a business day, not after the deadline. | verified, account loaded | create_promise | promise | yesterday, today, deadline, deadline + 1 | runtime (pre_promise) | test_enforcement Blocking; test_runtime Calls | EC-PR-05, EC-DT-01, EC-DT-03, EC-DT-04, EC-DT-05, EC-DT-07, EC-DT-10 | — |
| A-20 | A weekend day or holiday not after the deadline: offer the next business day. | as A-19 | create_promise | none | next business day would be after the deadline | runtime (block names the day); model | test_runtime Calls; live PUB-07 | EC-DT-04, EC-DT-05, EC-DT-06, EC-DT-10 | — |
| A-21 | A vague period: ask once for the exact day; none given: propose the deadline. | negotiating | none | none | — | model | live PUB-07 | EC-DT-11° | live only |
| A-22 | "Next <weekday>" is that weekday of the following week, "this <weekday>" the one of the current week. | negotiating | none | none | said on that weekday; week boundary | runtime (day names facts); model | test_platform DayNames; live PUB-06 | EC-DT-07, EC-DT-08, EC-DT-09 | "next" stays ambiguous on some days and is left to the read-back |
| A-23 | A date after the deadline: say the latest date and offer it; refusing it is a flat refusal. | negotiating | forbidden: create_promise after the deadline | none | customer insists | runtime (block); model | test_platform BlockedCalls; live PUB-04 | EC-DT-02, EC-DT-06, EC-MP-08, EC-CV-03 | — |
| A-24 | Ask for the full amount first. Never ask how much the customer can pay. | negotiating | none | none | — | model | live PUB-06 judge | — | live only |
| A-25 | Floor: minimum_payment for a credit card, instalment_amount for a loan. | account loaded | none | limits per product | — | runtime (account_facts) | test_runtime Calendar | EC-PR-01, EC-MP-02 | — |
| A-26 | Full amount too much: offer the floor once, say the rest stays overdue; refusal is a flat refusal. | negotiating | none | none | — | model | live PUB-06 | EC-CV-02 | live only |
| A-27 | An amount from the floor to the full amount, both included, is accepted; below the floor it cannot be recorded. | negotiating | create_promise | promise | floor - 0.01, floor, full, full + 0.01, zero, text | runtime (pre_promise) | test_enforcement Blocking | EC-PR-01, EC-PR-02, EC-PR-03, EC-PR-05, EC-MP-02 | — |
| A-28 | First ask "Can you make this payment today?". | debt stated | none | none | — | model | live judge | — | live only |
| A-29 | A date or amount the customer proposes is never a refusal. | negotiating | none | none | — | model | live PUB-04 | — | live only |
| A-30 | A flat refusal uses one persuasion attempt. Attempt 1: last business day of this week, or of next week when today is that day. Attempt 2, late band: floor on the deadline. | negotiating | none | none | today is the last business day of the week | runtime (dates as facts); model (counting) | test_runtime Calendar | EC-DT-09, EC-CV-05°, EC-CV-12° | attempts are counted by the model, not by code |
| A-31 | Attempts used up: stop, repeat no refused offer, close no_promise / refused. | negotiating | close_call | closed | — | model | none | EC-CV-03, EC-CV-05°, EC-CV-12° | live only |
| A-32 | Illness, death in the family, job loss: one sentence of sympathy, no persuasion, offer the support team. Accepts: transfer support, close transferred / support. Declines: no_promise / hardship. A date or amount the customer still names is recorded. | negotiating | transfer_to_agent, close_call, create_promise | transfer or closed | transfer fails; customer names a date anyway | model; runtime (close after a transfer) | live PUB-05 | EC-RT-05, EC-RT-07, EC-TR-04, EC-TR-05, EC-TR-08, EC-NT-08, EC-CV-10° | — |
| A-33 | Never offer a discount, a waiver, an instalment plan or any other term. | any time | none | none | customer presses | model | none | EC-CV-03, EC-CV-12° | live only |
| A-34 | Read product, amount and date back, ask "Do you confirm?", call create_promise only after a clear yes. | values agreed | create_promise | promise | unclear yes; yes then no | model | live PUB-01 | EC-CV-01, EC-CV-02, EC-CV-04, EC-CV-05° | the runtime cannot see consent |
| A-35 | A change at the read-back is checked again and read back again. | read-back | create_promise | promise | — | model; runtime (checks the final values) | none | EC-CV-01, EC-CV-02 | — |
| A-36 | One promise per product; a recorded promise cannot be changed or cancelled in the call. | promise recorded | forbidden: a second create_promise for the product | none | two identical calls in one message; invented product id | runtime (pre_promise) | test_enforcement Blocking; test_runtime Calls | EC-PR-04, EC-PR-07, EC-RT-10, EC-MP-05, EC-CV-01, EC-CV-08 | — |
| A-37 | Recorded only after ok true. On failure try once more; second failure: say so, close no_promise / service_error. | create_promise failed | create_promise once more | failed attempts per product | third attempt; failure seen only as an HTTP error | runtime (mark, limit); fixed failure rules in the prompt | test_platform HttpErrors; test_enforcement Failures; live PUB-10 | EC-RT-01, EC-RT-02, EC-RT-03, EC-MP-05 | — |
| A-38 | After a recorded promise say: "You will receive a text message confirming this promise." | promise recorded | none | none | — | model, told by the tool result | live PUB-01 judge | — | live only |
| A-39 | "I already paid": payments can take a business day to appear; no promise; close paid. | negotiating | close_call; forbidden: create_promise | closed | — | model | live PUB-08 | EC-NT-03 | — |
| A-40 | Not my debt, never had the product, closed or paid off in the past: no argument, no promise, close no_promise / dispute. | negotiating | close_call; forbidden: create_promise | closed | "paid off years ago" is a dispute, not paid | model | none | — | single-product dispute has no scenario; live only |
| A-41 | A new plan or lower instalments: describe no plan, transfer restructuring, close transferred / restructuring. | negotiating | transfer_to_agent, close_call | transfer; close forced | close sent in the same message | model; runtime (pre_close) | test_platform CallOrder; live PUB-09 | EC-TR-02, EC-TR-03, EC-CV-06° | — |
| A-42 | A request for a human: ask once whether you can help; insists: transfer general, close transferred / human. Before and after verification. | any time | transfer_to_agent, close_call | transfer; close forced | before verification | model; runtime (pre_close) | test_runtime Calls | EC-TR-01 | — |
| A-43 | "Is this a scam?": they can verify by phoning customer service; continue. | any time | none | none | asked repeatedly | model | none | EC-CV-07° | live only |
| A-44 | A question the tools cannot answer: say so. Invent no figures, dates or reasons. | any time | none | none | account lookup failed | model; runtime (says the lookup failed) | test_enforcement Failures | EC-PR-06 | — |
| A-45 | Says twice they do not want to talk: no_promise / refused; before verification not_verified / declined. | any time | close_call | closed | — | model | none | EC-TR-01, EC-CV-11° | live only |
| A-46 | Exactly one close_call, the last tool call, with outcome and the reason the table lists. A transfer after a recorded promise closes as transferred. | call ending | close_call; forbidden: any tool after it | closed | impossible pair; missing reason; reason not in the table; close fails | graph (ends after a successful close); runtime (pre_close) | test_enforcement Blocking, Failures; test_runtime Calls | EC-RT-04, EC-RT-08, EC-TR-01, EC-TR-02, EC-TR-03, EC-TR-05, EC-TR-08, EC-CL-01, EC-CL-02, EC-CL-03, EC-CL-04, EC-CL-05, EC-CL-06, EC-CL-07 | a close is refused once per problem and then let through, so that a call can always end |
| A-47 | Thank the customer and say goodbye before close_call. | call ending | none | none | — | model | live judge | — | live only |

## Brief B (official, up to three products)

Source: `briefs/brief-b.md` of the starter kit, rules 1 to 11; B-12 is its closing section. Every Brief A rule still applies unless replaced.

| Rule | Required behaviour | Preconditions | Tools (allowed; forbidden) | State change | Edge cases | Enforced by | Existing tests | Benchmark | Missing |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| B-01 | get_account returns one to three products, each with its own past_due, days_late and floor. | account loaded | none | limits per product | unknown product id; product named by its label | runtime | test_runtime Calls | EC-MP-02, EC-MP-03, EC-MP-07 | — |
| B-02 | State every late product, one sentence each, before asking for anything. | account loaded | none | none | — | model | live PUB-11 | — | live only |
| B-03 | Band and deadline are per product. | account loaded | none | limits per product | two bands in one account | runtime | test_runtime Calls; live PUB-13 | EC-MP-01 | — |
| B-04 | Ask whether all can be paid today; if not go product by product in get_account order, without asking about today again; each starts at attempt 1. | negotiating | none | none | — | model | live PUB-12 | — | live only |
| B-05 | Persuasion attempts are counted per product. | negotiating | none | none | — | model | live PUB-12 | EC-MP-08 | counted by the model |
| B-06 | One date for everything that is after one product's deadline: that product goes on its own deadline, said in the read-back. | negotiating | create_promise | promises | customer refuses the earlier date | runtime (block per product); model | live PUB-13 | EC-MP-01, EC-MP-08, EC-MP-09° | — |
| B-07 | Read all promises back together, one confirmation, then create_promise once per product. | values agreed | create_promise per product | promises | — | model; runtime (one per product) | live PUB-11 | EC-MP-03, EC-MP-09° | — |
| B-08 | Each product without a promise gets add_note with reason refused, hardship, dispute, paid or service_error, also when nothing is promised. | product ended without a promise | add_note | notes | second note; note on a promised product; close before the notes; reason outside the list | runtime (pre_note: duplicates, reason; pre_close: open products, once) | test_runtime Calls | EC-MP-04, EC-MP-06, EC-MP-07, EC-MP-08, EC-NT-01, EC-NT-07, EC-NT-08, EC-NT-09, EC-NT-10 | a close with open products is refused once and then let through |
| B-09 | A dispute or "already paid" on one product does not end the call. | several products | add_note, then continue | note | — | model | none | EC-MP-04, EC-NT-02 | — |
| B-10 | Restructuring still transfers. Recorded promises stay; agreed but unrecorded ones are not recorded; close transferred / restructuring. On any transfer no notes at all. | transfer done | forbidden: create_promise, add_note | none | promise and transfer close together | runtime (pre_promise, pre_note, pre_close) | test_enforcement Blocking | EC-TR-06, EC-TR-07 | — |
| B-11 | create_promise fails twice for a product: keep the recorded promises, add_note service_error for it. | second failure | add_note | note | failures of one product must not count for another | runtime; model (adding the note) | test_enforcement Failures; test_platform HttpErrors | EC-MP-05, EC-NT-06 | — |
| B-12 | Closing with no promise: all noted paid is paid; otherwise no_promise with the reason of the first product, in get_account order, not noted paid. A promise wins, except after a transfer. | call ending | close_call | closed | notes added in another order | runtime (pre_close) | test_runtime Calls | EC-TR-07, EC-MP-04, EC-NT-01, EC-NT-02, EC-NT-03, EC-NT-04, EC-NT-05, EC-NT-06, EC-NT-08, EC-CL-08 | — |

## Platform behaviour

Source: live calls of 2026-10-10 and the kit's `docs/tools-and-code.md`.

| Rule | Required behaviour | Preconditions | Tools (allowed; forbidden) | State change | Edge cases | Enforced by | Existing tests | Benchmark | Missing |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| P-01 | The engine runs no post-hook after an HTTP error. What to do after a failed or refused call must still reach the model. | a tool answers with an HTTP error | the same tool again, within its limit | mark of the lost call | every tool | runtime (mark, live state); fixed failure rules in the prompt | test_platform HttpErrors | EC-ID-06, EC-RT-01, EC-RT-02, EC-RT-07, EC-RT-08, EC-RT-09, EC-TR-04, EC-NT-09 | whether the model reads the failure rules is live only |
| P-02 | Tool calls of one model message are not run in the order written. close_call must not overtake another call. | two calls in one message | forbidden: close_call ahead of the other call | none | transfer, callback or promise sent with the close; two calls of one tool | runtime (a call waits while another of the same message has no result) | test_platform CallOrder | EC-RT-04, EC-RT-10, EC-TR-03, EC-TR-09 | assumes one pre-hook sees what the other wrote; not yet seen live |
| P-03 | The mark of a lost call must not block the call for good. | a call failed with an HTTP error | close_call | mark cleared | no measurable time between the calls | runtime (age of the mark; held once at most) | test_platform CallOrder | EC-RT-05, EC-RT-06 | — |
| P-04 | State handed in with the call is discarded. | call starts | none | state reset | — | runtime (setup) | test_enforcement Authorization | EC-ID-07 | — |
| P-05 | A refused call records nothing, and a valid call right after it is reported as it happened. | a call was refused | the corrected call | none | refusal with and without an answer from the bank | runtime | test_platform BlockedCalls | EC-PR-01, EC-DT-02 | the bank's answer to a refused request is assumed, not yet seen live |

## Brief C (synthetic)

Source: `benchmarks/synthetic_briefs/brief-c.md`, written for this benchmark. It is not hackathon material. "Fits agent-spec/1" says whether the current schema can carry the rule as structure: `yes`, `partly`, `no`, or `rule text` for a rule that is about the conversation and is text in every brief.

| Rule | Required behaviour | Fits agent-spec/1 | How, or what is missing | Benchmark |
| --- | --- | --- | --- | --- |
| C-01 | Opening sentence; ask for first and last name; address by first name; no arrears, money or product before verification. | yes | opening_line, rules text | SC-12° |
| C-02 | Someone else answers: say you will call again, close not_reached / no_callback or wrong_number. schedule_callback is never used. | yes | no callback section, so the tool is not offered | SC-01 |
| C-03 | Two questions in order: postcode, then year of birth. | yes | identity.questions, with a tool API that lists these fields | SC-02 |
| C-04 | Send the postcode in capitals without spaces, the year as four digits. | partly | digits are normalised; text answers are only trimmed | SC-04* |
| C-05 | Two wrong answers in total end the check; close not_verified / failed. | yes | identity.max_wrong; the lower of the brief's and the service's limit ends the check | SC-03 |
| C-06 | Declines to answer: close not_verified / declined at once. | rule text | conversation rule | SC-12° |
| C-07 | After both pass, get_account. Product names: "member loan", "store card ending NNNN". | yes | account.values label | SC-02 |
| C-08 | A promise only when plan_active is false and days_late is 60 or less; otherwise transfer to arrears_team, close transferred / arrears. | no | no eligibility condition in the schema | SC-08* |
| C-09 | Business days Monday to Saturday; four holidays. | yes | calendar | SC-06 |
| C-10 | Deadline: fifth calendar day after today for 1 to 30 days late, second for more; rolled back to a business day. | yes | calendar_days_after with roll previous_business | SC-06, SC-10 |
| C-11 | A promise date is tomorrow or later, never today. | no | no earliest date in account.limits | SC-07* |
| C-12 | Floor: 40 percent of the past-due amount for a store card, 25 percent for a member loan. Maximum: the full amount. | yes | percent expressions | SC-05, SC-10 |
| C-13 | Full amount on the deadline first; one persuasion attempt with the floor on the deadline; then no_promise / refused. | rule text | conversation rule; dates and amounts are facts | — |
| C-14 | Read back, "Is that correct?", create_promise after a clear yes; one promise. | rule text | conversation rule; one promise per product is enforced | — |
| C-15 | If create_promise fails, do not retry; close no_promise / service_error. | yes | promise.max_attempts 1 | SC-09 |
| C-16 | After a recorded promise say "Your promise is on file." | yes | guidance promise.recorded_all | — |
| C-17 | "Already paid": close paid. | rule text | conversation rule | — |
| C-18 | A dispute or a request for a person: transfer general at once, close transferred / general, before or after verification. | yes | transfer tool in both parts; close.forced | SC-11 |
| C-19 | add_note is never used. | yes | no note section | — |
| C-20 | Outcome table with its own reasons. | yes | close.outcomes | — |

## Brief D (synthetic)

Source: `benchmarks/synthetic_briefs/brief-d.md`, written for this benchmark. It is not hackathon material. "Fits agent-spec/1" says whether the current schema can carry the rule as structure: `yes`, `partly`, `no`, or `rule text` for a rule that is about the conversation and is text in every brief.

| Rule | Required behaviour | Fits agent-spec/1 | How, or what is missing | Benchmark |
| --- | --- | --- | --- | --- |
| D-01 | Opening sentence; nothing about an account before verification. | yes | opening_line, rules text | — |
| D-02 | Callback only on one of the next two business days, never today, 09:00 to 17:00. | yes | callback.include_today false, next_business_days 2 | SD-11 |
| D-03 | Identity as in the kit: date of birth, phone digits, three wrong answers. | yes | identity | — |
| D-04 | One to four products: mortgage, credit card, overdraft; field secured. | yes | account.values label, with a tool API that lists these types | — |
| D-05 | State every product before asking for anything. | rule text | conversation rule | SD-12° |
| D-06 | Secured products first. No promise on an unsecured product while a secured one has neither promise nor note. | no | no dependency between products | SD-07*, SD-12° |
| D-07 | Floor: a mortgage only in full; credit card 30 percent; overdraft 50 percent. | yes | by type, field and percent | SD-01 |
| D-08 | Business days Monday to Friday; three holidays. | yes | calendar | — |
| D-09 | Deadline: mortgage third business day; others tenth for 1 to 14 days late, fifth above. | yes | by type, then by days_late | SD-02 |
| D-10 | A promise date is today or later, a business day, not after the product's deadline. | yes | limits and business_day_only | SD-02 |
| D-11 | The promises of one call must add up to at least 40 percent of the total past due; otherwise none is recorded. | no | no limit across products | SD-08* |
| D-12 | Read all back together, one confirmation, one create_promise per product. | rule text | conversation rule | SD-12° |
| D-13 | Up to three attempts per product when create_promise fails; then a service_error note. | yes | promise.max_attempts 3 | SD-05, SD-06 |
| D-14 | Every product without a promise gets a note: refused, dispute, paid, deferred, service_error. | partly | notes and their effect on the close are structured; that every product gets one is rule text | SD-03, SD-06, SD-10 |
| D-15 | A plan request transfers to restructuring; notes are added after the transfer; no promise after it. | yes | note.forbidden_after_transfer false | SD-03, SD-04 |
| D-16 | A request for a human: ask once, then general; close transferred / human. | yes | close.queue_reasons | — |
| D-17 | If any product is noted dispute, close no_promise / dispute even when promises were recorded. | no | no closing flag for "a note with this reason exists" | SD-09* |
| D-18 | Otherwise: a promise closes as promise; no promise and all paid is paid; else the reason of the first note that is not paid. | yes | close.forced | SD-09*, SD-10 |
| D-19 | Outcome table with its own reasons. | yes | close.outcomes | — |

## Brief E (synthetic)

Source: `benchmarks/synthetic_briefs/brief-e.md`, written for this benchmark. It is not hackathon material. "Fits agent-spec/1" says whether the current schema can carry the rule as structure: `yes`, `partly`, `no`, or `rule text` for a rule that is about the conversation and is text in every brief.

| Rule | Required behaviour | Fits agent-spec/1 | How, or what is missing | Benchmark |
| --- | --- | --- | --- | --- |
| E-01 | Opening sentence; nothing about an account before verification. | yes | opening_line, rules text | — |
| E-02 | Callback only on the next business day, 08:00 to 12:00. | yes | callback | SE-11 |
| E-03 | Identity as in the kit. | yes | identity | — |
| E-04 | One or two accounts: electricity, gas; field vulnerable. | yes | account.values label | — |
| E-05 | Business days Sunday to Thursday; two holidays. | yes | calendar.business_weekdays, week_start | SE-01 |
| E-06 | A promise date is today or later, a business day, not after the deadline. | yes | limits | SE-01 |
| E-07 | Deadline: the last business day of the current month. | yes | end_of_month with roll | SE-02, SE-03 |
| E-08 | Never more than ten calendar days from today; where rule 7 is later, rule 8 wins. | yes | earliest of two dates | SE-02, SE-03 |
| E-09 | Replaces 7 and 8 above 30 days late: the next business day. | yes | by days_late | SE-04 |
| E-10 | After 16:00 no promise for today; earliest is the next business day. | no | no earliest date and no condition on the time of day | SE-10* |
| E-11 | Floor: electricity 35.00; gas 20 percent of the past-due amount. | yes | fixed_amount, percent | SE-05, SE-12 |
| E-12 | Replaces 11 for a vulnerable account: floor 10.00. | yes | by vulnerable, first case | SE-05 |
| E-13 | Always propose the full amount first. | rule text | conversation rule | SE-13° |
| E-14 | Replaces 13 for a vulnerable account: never propose the full amount, start with the floor. | rule text | conversation rule; the extraction has to keep the override | SE-13° |
| E-15 | Read back, clear yes, create_promise; one retry; then no_promise / service_error. | yes | promise.max_attempts 2 | — |
| E-16 | Escalations by priority: welfare, then complaints, then plans. Exactly one transfer per call. | partly | queues and reasons are structured; the priority is rule text; a second transfer is not stopped | SE-06*, SE-13° |
| E-17 | After a transfer nothing more is recorded. | yes | promise.forbidden_after_transfer | SE-07*, SE-08 |
| E-18 | Replaces 17 for the plans queue: a confirmed promise may still be recorded. | no | the switch is not per queue | SE-07* |
| E-19 | A transfer closes as transferred with the reason of its queue. | yes | close.forced, queue_reasons | SE-08, SE-09 |
| E-20 | Replaces 19: a failed identity check closes as not_verified / failed even after a transfer. | yes | order of close.forced | SE-09 |
| E-21 | add_note is never used. | yes | no note section | — |
| E-22 | Outcome table with its own reasons. | yes | close.outcomes | — |

## Rules without any benchmark scenario

A-02, A-07, A-09, A-17, A-24, A-28, A-29, A-38, A-40, A-47, B-02, B-04, C-13, C-14, C-16, C-17, C-19, C-20, D-01, D-03, D-04, D-08, D-16, D-19, E-01, E-03, E-04, E-15, E-21, E-22

These are rules about wording or about recognising what a person means. They need a live run with a judge; see the Missing column.
