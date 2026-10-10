# Rule-level evaluation

Run at 2026-10-11 01:39:57, offline. All packages are synthetic and author-prepared, not independently validated.

## Package c (development, v1): reference spec

| | |
| --- | --- |
| Rules | 20 |
| First failing layer | conversational 3, none 14, schema 3 |
| End to end | encoded, enforcement not played 4, enforced 8, not evaluated offline 3, partial 1, text only 2, unsupported 2 |
| Checks through the compiled workflow | 16 of 19 pass |
| of these: policy (prescribed values, records after compliant calls) | 9 of 9 pass |
| of these: enforcement (a call no compliant agent makes is stopped or corrected) | 7 of 10 pass |
| Critical rules | 16; not evaluated offline: none; weak evidence only: C-16; marked not confirmed by behaviour: none |
| Requests checked against the tool API | 74 sent, 6 blocked by policy, 0 kind(s) of problem |

Policy, enforcement and weak evidence are counted per rule as checks passed of checks linked, for the reference spec. They are different kinds of evidence and are not added up.

| Rule | Category | Critical | Schema fit | Reference | Extraction | Policy | Enforcement | Weak | First failing layer | End to end | Note |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| C-01 | opening_and_who_is_on_the_line | yes | structured | passes | - | 1/1 | none | 2/2 | - | encoded, enforcement not played |  |
| C-02 | opening_and_who_is_on_the_line | yes | structured | passes | - | 1/1 | 1/1 | none | - | enforced |  |
| C-03 | identity | yes | structured | passes | - | 1/1 | 1/1 | none | - | enforced |  |
| C-04 | identity | yes | partial | carries none of what the checks ask | - | none | 0/1 | none | schema | partial |  |
| C-05 | identity | yes | structured | passes | - | 1/1 | 1/1 | none | - | enforced |  |
| C-06 | identity |  | text | not evaluated | not evaluated | none | none | none | conversational | not evaluated offline |  |
| C-07 | the_account | yes | structured | passes | - | 2/2 | 1/1 | none | - | enforced |  |
| C-08 | the_account | yes | unsupported | cannot carry the rule | - | none | 0/1 | none | schema | unsupported |  |
| C-09 | dates | yes | structured | passes | - | 2/2 | 1/1 | none | - | enforced |  |
| C-10 | dates | yes | structured | passes | - | 4/4 | 2/2 | none | - | enforced |  |
| C-11 | dates | yes | unsupported | cannot carry the rule | - | none | 0/1 | none | schema | unsupported |  |
| C-12 | amounts | yes | structured | passes | - | 2/2 | 2/2 | none | - | enforced |  |
| C-13 | amounts |  | text | not evaluated | not evaluated | none | none | none | conversational | not evaluated offline |  |
| C-14 | recording |  | text | passes (text only, weak evidence) | - | none | none | 1/1 | - | text only |  |
| C-15 | recording | yes | structured | passes | - | 1/1 | 1/1 | none | - | enforced |  |
| C-16 | recording | yes | structured | passes (text only, weak evidence) | - | none | none | 1/1 | - | text only |  |
| C-17 | other_things_members_say |  | text | not evaluated | not evaluated | none | none | none | conversational | not evaluated offline |  |
| C-18 | other_things_members_say | yes | structured | passes | - | 2/2 | none | none | - | encoded, enforcement not played |  |
| C-19 | other_things_members_say | yes | structured | passes | - | 1/1 | none | none | - | encoded, enforcement not played |  |
| C-20 | closing | yes | structured | passes | - | 1/1 | none | none | - | encoded, enforcement not played |  |

Failed checks:

- SC-04 (calls; C-04): compiled level: step 1 (verify_identity): tool result lacks 'That answer is correct' (it says: That answer is WRONG. Wrong answers left before the check ends: 1. Do not move to another question. Ask the same question again: "the postcode of your registered address" (field postcode).) / compiled level: step 3 (check): the call is in contact, expected negotiate / compiled level: step 3 (check): state wrong is 1, expected 0
- SC-07 (calls; C-11): compiled level: step 3 (create_promise): expected refused, got ok / compiled level: step 4 (create_promise): expected ok, got refused / compiled level: ledger promises is [('p1', 300.0, '2026-10-14')], expected [('p1', 300.0, '2026-10-15')]
- SC-08 (calls; C-08): compiled level: step 3 (create_promise): expected blocked, got ok / compiled level: ledger promises is [('p1', 500.0, '2026-10-16')], expected []

## Package d (development, v1): reference spec

| | |
| --- | --- |
| Rules | 19 |
| First failing layer | conversational 2, none 14, schema 3 |
| End to end | encoded, enforcement not played 6, enforced 7, not evaluated offline 2, partial 1, unsupported 3 |
| Checks through the compiled workflow | 19 of 22 pass |
| of these: policy (prescribed values, records after compliant calls) | 11 of 11 pass |
| of these: enforcement (a call no compliant agent makes is stopped or corrected) | 8 of 11 pass |
| Critical rules | 17; not evaluated offline: none; weak evidence only: none; marked not confirmed by behaviour: none |
| Requests checked against the tool API | 108 sent, 8 blocked by policy, 0 kind(s) of problem |

Policy, enforcement and weak evidence are counted per rule as checks passed of checks linked, for the reference spec. They are different kinds of evidence and are not added up.

| Rule | Category | Critical | Schema fit | Reference | Extraction | Policy | Enforcement | Weak | First failing layer | End to end | Note |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| D-01 | opening_and_who_is_on_the_line | yes | structured | passes | - | 1/1 | none | 2/2 | - | encoded, enforcement not played |  |
| D-02 | opening_and_who_is_on_the_line | yes | structured | passes | - | 1/1 | 1/1 | none | - | enforced |  |
| D-03 | identity | yes | structured | passes | - | 2/2 | none | none | - | encoded, enforcement not played |  |
| D-04 | the_products | yes | structured | passes | - | 3/3 | none | none | - | encoded, enforcement not played |  |
| D-05 | the_products |  | text | not evaluated | not evaluated | none | none | none | conversational | not evaluated offline |  |
| D-06 | the_products | yes | unsupported | cannot carry the rule | - | none | 0/1 | none | schema | unsupported |  |
| D-07 | amounts_and_dates | yes | structured | passes | - | 3/3 | 1/1 | none | - | enforced |  |
| D-08 | amounts_and_dates | yes | structured | passes | - | 2/2 | none | none | - | encoded, enforcement not played |  |
| D-09 | amounts_and_dates | yes | structured | passes | - | 5/5 | 1/1 | none | - | enforced |  |
| D-10 | amounts_and_dates | yes | structured | passes | - | none | 1/1 | none | - | enforced |  |
| D-11 | amounts_and_dates | yes | unsupported | cannot carry the rule | - | none | 0/1 | none | schema | unsupported |  |
| D-12 | recording |  | text | not evaluated | not evaluated | none | none | none | conversational | not evaluated offline |  |
| D-13 | recording | yes | structured | passes | - | 1/1 | 2/2 | none | - | enforced |  |
| D-14 | notes | yes | partial | passes | - | 1/1 | 3/3 | none | - | partial |  |
| D-15 | notes | yes | structured | passes | - | 1/1 | 2/2 | none | - | enforced |  |
| D-16 | notes | yes | structured | passes | - | 1/1 | none | none | - | encoded, enforcement not played |  |
| D-17 | closing | yes | unsupported | cannot carry the rule | - | none | 0/1 | none | schema | unsupported |  |
| D-18 | closing | yes | structured | passes | - | none | 1/1 | none | - | enforced |  |
| D-19 | closing | yes | structured | passes | - | 1/1 | none | none | - | encoded, enforcement not played |  |

Failed checks:

- SD-07 (calls; D-06): compiled level: step 3 (create_promise): expected blocked, got ok / compiled level: step 5 (create_promise): expected ok, got refused
- SD-08 (calls; D-11): compiled level: step 4 (create_promise): expected blocked, got ok / compiled level: step 5 (add_note): expected ok, got refused / compiled level: ledger outcome is 'promise', expected 'no_promise' / compiled level: ledger reason is None, expected 'refused' / compiled level: ledger promises is [('p2', 180.0, '2026-10-16')], expected [] / compiled level: ledger notes is [('p1', 'refused')], expected [('p1', 'refused'), ('p2', 'refused')]
- SD-09 (calls; D-17): compiled level: ledger outcome is 'promise', expected 'no_promise' / compiled level: ledger reason is None, expected 'dispute'

## Package e (development, v1): reference spec

| | |
| --- | --- |
| Rules | 22 |
| First failing layer | conversational 2, none 17, schema 3 |
| End to end | encoded, enforcement not played 6, enforced 11, not evaluated offline 2, partial 1, unsupported 2 |
| Checks through the compiled workflow | 20 of 23 pass |
| of these: policy (prescribed values, records after compliant calls) | 11 of 11 pass |
| of these: enforcement (a call no compliant agent makes is stopped or corrected) | 9 of 12 pass |
| Critical rules | 20; not evaluated offline: none; weak evidence only: none; marked not confirmed by behaviour: none |
| Requests checked against the tool API | 103 sent, 11 blocked by policy, 0 kind(s) of problem |

Policy, enforcement and weak evidence are counted per rule as checks passed of checks linked, for the reference spec. They are different kinds of evidence and are not added up.

| Rule | Category | Critical | Schema fit | Reference | Extraction | Policy | Enforcement | Weak | First failing layer | End to end | Note |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| E-01 | opening_and_who_is_on_the_line | yes | structured | passes | - | 1/1 | none | 2/2 | - | encoded, enforcement not played |  |
| E-02 | opening_and_who_is_on_the_line | yes | structured | passes | - | 1/1 | 1/1 | none | - | enforced |  |
| E-03 | identity | yes | structured | passes | - | 2/2 | none | none | - | encoded, enforcement not played |  |
| E-04 | the_accounts | yes | structured | passes | - | 2/2 | none | none | - | encoded, enforcement not played |  |
| E-05 | dates | yes | structured | passes | - | 2/2 | 1/1 | none | - | enforced |  |
| E-06 | dates | yes | structured | passes | - | none | 1/1 | none | - | enforced |  |
| E-07 | dates | yes | structured | passes | - | 1/1 | 2/2 | none | - | enforced |  |
| E-08 | dates | yes | structured | passes | - | 1/1 | 2/2 | none | - | enforced |  |
| E-09 | dates | yes | structured | passes | - | 3/3 | 1/1 | none | - | enforced |  |
| E-10 | dates | yes | unsupported | cannot carry the rule | - | none | 0/1 | none | schema | unsupported |  |
| E-11 | amounts | yes | structured | passes | - | 2/2 | 2/2 | none | - | enforced |  |
| E-12 | amounts | yes | structured | passes | - | 2/2 | 1/1 | none | - | enforced |  |
| E-13 | amounts |  | text | not evaluated | not evaluated | none | none | none | conversational | not evaluated offline |  |
| E-14 | amounts |  | text | not evaluated | not evaluated | none | none | none | conversational | not evaluated offline |  |
| E-15 | recording | yes | structured | passes | - | 1/1 | none | none | - | encoded, enforcement not played |  |
| E-16 | handing_the_call_over | yes | partial | carries none of what the checks ask | - | none | 0/1 | none | schema | partial |  |
| E-17 | handing_the_call_over | yes | structured | passes | - | none | 1/1 | none | - | enforced |  |
| E-18 | handing_the_call_over | yes | unsupported | cannot carry the rule | - | none | 0/1 | none | schema | unsupported |  |
| E-19 | closing | yes | structured | passes | - | 1/1 | 2/2 | none | - | enforced |  |
| E-20 | closing | yes | structured | passes | - | none | 1/1 | none | - | enforced |  |
| E-21 | closing | yes | structured | passes | - | 1/1 | none | none | - | encoded, enforcement not played |  |
| E-22 | closing | yes | structured | passes | - | 1/1 | none | none | - | encoded, enforcement not played |  |

Failed checks:

- SE-06 (calls; E-16): compiled level: step 4 (transfer_to_agent): expected blocked, got ok / compiled level: ledger reason is 'complaint', expected 'plan' / compiled level: ledger transfers is ['plans', 'complaints'], expected ['plans']
- SE-07 (calls; E-18): compiled level: step 4 (create_promise): expected ok, got refused / compiled level: ledger promises is [], expected [('p1', 180.0, '2026-10-15')]
- SE-10 (calls; E-10): compiled level: step 3 (create_promise): expected refused, got ok / compiled level: step 4 (create_promise): expected ok, got refused / compiled level: ledger promises is [('p1', 180.0, '2026-10-14')], expected [('p1', 180.0, '2026-10-15')]

## Package f (selection, v1): reference spec

| | |
| --- | --- |
| Rules | 58 |
| First failing layer | compiler/runtime 2, conversational 22, none 32, schema 2 |
| End to end | encoded, enforcement not played 12, enforced 18, failed 2, not evaluated offline 22, text only 2, unsupported 2 |
| Checks through the compiled workflow | 33 of 37 pass |
| of these: policy (prescribed values, records after compliant calls) | 15 of 15 pass |
| of these: enforcement (a call no compliant agent makes is stopped or corrected) | 18 of 22 pass |
| Critical rules | 38; not evaluated offline: F-03, F-42, F-44, F-48; weak evidence only: F-21; marked not confirmed by behaviour: F-21 |
| Requests checked against the tool API | 152 sent, 17 blocked by policy, 0 kind(s) of problem |

Policy, enforcement and weak evidence are counted per rule as checks passed of checks linked, for the reference spec. They are different kinds of evidence and are not added up.

| Rule | Category | Critical | Schema fit | Reference | Extraction | Policy | Enforcement | Weak | First failing layer | End to end | Note |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| F-01 | opening | yes | structured | passes | - | 1/1 | none | none | - | encoded, enforcement not played |  |
| F-02 | opening |  | structured | passes | - | 1/1 | none | none | - | encoded, enforcement not played |  |
| F-03 | opening | yes | text | not evaluated | not evaluated | none | none | none | conversational | not evaluated offline |  |
| F-04 | callback | yes | structured | encodes the rule, the compiled workflow does not behave so | - | 1/1 | 0/1 | none | compiler/runtime | failed |  |
| F-05 | callback |  | text | not evaluated | not evaluated | none | none | none | conversational | not evaluated offline |  |
| F-06 | identity | yes | structured | passes | - | 1/1 | none | none | - | encoded, enforcement not played |  |
| F-07 | identity | yes | structured | passes | - | none | 1/1 | none | - | enforced |  |
| F-08 | identity | yes | structured | passes | - | 1/1 | 1/1 | none | - | enforced |  |
| F-09 | identity |  | text | not evaluated | not evaluated | none | none | none | conversational | not evaluated offline |  |
| F-10 | accounts | yes | structured | passes | - | 3/3 | none | none | - | encoded, enforcement not played |  |
| F-11 | calendar | yes | structured | passes | - | 2/2 | none | none | - | encoded, enforcement not played |  |
| F-12 | calendar | yes | structured | passes | - | 1/1 | none | none | - | encoded, enforcement not played |  |
| F-13 | dates | yes | structured | passes | - | 4/4 | 1/1 | none | - | enforced |  |
| F-14 | dates | yes | structured | passes | - | none | 1/1 | none | - | enforced |  |
| F-15 | amounts | yes | structured | passes | - | 2/2 | none | none | - | encoded, enforcement not played |  |
| F-16 | amounts | yes | structured | passes | - | 2/2 | none | none | - | encoded, enforcement not played |  |
| F-17 | amounts | yes | structured | passes | - | 1/1 | 1/1 | none | - | enforced |  |
| F-18 | dates | yes | unsupported | cannot carry the rule | - | none | 0/1 | none | schema | unsupported |  |
| F-19 | amounts | yes | unsupported | cannot carry the rule | - | none | 0/1 | none | schema | unsupported |  |
| F-20 | negotiation |  | text | not evaluated | not evaluated | none | none | none | conversational | not evaluated offline |  |
| F-21 | recording | yes | text | passes (text only, weak evidence) | - | none | none | 2/2 | - | text only | not confirmed by behaviour offline: Whether the customer said yes before create_promise is called can only be seen in a live call. Offline it is shown only that the read-back question is in the spec and in the prompt of the step in which create_promise exists. |
| F-22 | recording |  | text | passes (text only, weak evidence) | - | none | none | 1/1 | - | text only |  |
| F-23 | recording | yes | structured | passes | - | none | 1/1 | none | - | enforced |  |
| F-24 | recording | yes | structured | passes | - | 2/2 | 1/1 | none | - | enforced |  |
| F-25 | notes | yes | structured | encodes the rule, the compiled workflow does not behave so | - | 2/2 | 0/1 | none | compiler/runtime | failed |  |
| F-26 | notes |  | text | not evaluated | not evaluated | none | none | none | conversational | not evaluated offline |  |
| F-27 | handover | yes | structured | passes | - | none | 1/1 | none | - | enforced |  |
| F-28 | handover | yes | structured | passes | - | 1/1 | 1/1 | none | - | enforced |  |
| F-29 | closing | yes | structured | passes | - | 1/1 | 1/1 | none | - | enforced |  |
| F-30 | closing | yes | structured | passes | - | 1/1 | 1/1 | none | - | enforced |  |
| F-31 | closing | yes | structured | passes | - | 1/1 | 1/1 | none | - | enforced |  |
| F-32 | handover | yes | structured | passes | - | 1/1 | 1/1 | none | - | enforced |  |
| F-33 | handover |  | text | not evaluated | not evaluated | none | none | none | conversational | not evaluated offline |  |
| F-34 | negotiation |  | text | not evaluated | not evaluated | none | none | none | conversational | not evaluated offline |  |
| F-35 | call | yes | structured | passes | - | 1/1 | none | none | - | encoded, enforcement not played |  |
| F-36 | closing | yes | structured | passes | - | 1/1 | 1/1 | none | - | enforced |  |
| F-37 | closing | yes | structured | passes | - | 1/1 | 1/1 | none | - | enforced |  |
| F-38 | closing | yes | structured | passes | - | 1/1 | 1/1 | none | - | enforced |  |
| F-39 | closing |  | text | not evaluated | not evaluated | none | none | none | conversational | not evaluated offline |  |
| F-40 | handover | yes | structured | passes | - | 1/1 | 1/1 | none | - | enforced |  |
| F-41 | handover | yes | structured | passes | - | 1/1 | 1/1 | none | - | enforced |  |
| F-42 | third_party | yes | text | not evaluated | not evaluated | none | none | none | conversational | not evaluated offline |  |
| F-43 | third_party |  | text | not evaluated | not evaluated | none | none | none | conversational | not evaluated offline |  |
| F-44 | handover | yes | text | not evaluated | not evaluated | none | none | none | conversational | not evaluated offline |  |
| F-45 | handover |  | text | not evaluated | not evaluated | none | none | none | conversational | not evaluated offline |  |
| F-46 | identity |  | text | not evaluated | not evaluated | none | none | none | conversational | not evaluated offline |  |
| F-47 | accounts | yes | structured | passes | - | 1/1 | none | none | - | encoded, enforcement not played |  |
| F-48 | recording | yes | text | not evaluated | not evaluated | none | none | none | conversational | not evaluated offline |  |
| F-49 | documents |  | text | not evaluated | not evaluated | none | none | none | conversational | not evaluated offline |  |
| F-50 | documents |  | text | not evaluated | not evaluated | none | none | none | conversational | not evaluated offline |  |
| F-51 | call | yes | structured | passes | - | 1/1 | none | none | - | encoded, enforcement not played |  |
| F-52 | negotiation |  | text | not evaluated | not evaluated | none | none | none | conversational | not evaluated offline |  |
| F-53 | identity | yes | structured | passes | - | 1/1 | none | none | - | encoded, enforcement not played |  |
| F-54 | notes |  | text | not evaluated | not evaluated | none | none | none | conversational | not evaluated offline |  |
| F-55 | notes |  | text | not evaluated | not evaluated | none | none | none | conversational | not evaluated offline |  |
| F-56 | notes |  | text | not evaluated | not evaluated | none | none | none | conversational | not evaluated offline |  |
| F-57 | notes |  | text | not evaluated | not evaluated | none | none | none | conversational | not evaluated offline |  |
| F-58 | notes |  | text | not evaluated | not evaluated | none | none | none | conversational | not evaluated offline |  |

Failed checks:

- F.c1 (calls; F-04): compiled level: step 1 (schedule_callback): expected blocked, got ok / compiled level: step 2 (schedule_callback): expected blocked, got ok / compiled level: step 3 (schedule_callback): expected blocked, got ok / compiled level: step 4 (schedule_callback): expected blocked, got ok / compiled level: ledger callbacks is [('2026-10-14', '15:00'), ('2026-10-15', '09:29'), ('2026-10-15', '17:30'), ('2026-10-15', '17:31'), ('2026-10-19', '10:00')], expected [('2026-10-15', '17:30')]
- F.c7 (calls; F-18): compiled level: step 3 (create_promise): expected blocked, got ok / compiled level: step 4 (create_promise): expected ok, got refused / compiled level: ledger promises is [('p1', 200.0, '2026-10-14')], expected [('p1', 400.0, '2026-10-14')]
- F.c8 (calls; F-19): compiled level: step 3 (create_promise): expected blocked, got ok / compiled level: step 4 (create_promise): expected ok, got refused / compiled level: ledger promises is [('p1', 215.0, '2026-10-15')], expected [('p1', 220.0, '2026-10-15')]
- F.c25 (calls; F-25): compiled level: step 4 (close_call): expected blocked, got ok / compiled level: step 5 (add_note): expected ok, got unavailable / compiled level: step 6 (close_call): expected ok, got unavailable / compiled level: ledger notes is [], expected [('p2', 'declined')]

## Package g (final, v1): reference spec

| | |
| --- | --- |
| Rules | 24 |
| First failing layer | conversational 3, none 19, schema 2 |
| End to end | encoded, enforcement not played 7, enforced 8, not evaluated offline 3, partial 2, text only 2, unsupported 2 |
| Checks through the compiled workflow | 27 of 29 pass |
| of these: policy (prescribed values, records after compliant calls) | 15 of 15 pass |
| of these: enforcement (a call no compliant agent makes is stopped or corrected) | 12 of 14 pass |
| Critical rules | 19; not evaluated offline: none; weak evidence only: none; marked not confirmed by behaviour: G-18 |
| Requests checked against the tool API | 129 sent, 15 blocked by policy, 0 kind(s) of problem |

Policy, enforcement and weak evidence are counted per rule as checks passed of checks linked, for the reference spec. They are different kinds of evidence and are not added up.

| Rule | Category | Critical | Schema fit | Reference | Extraction | Policy | Enforcement | Weak | First failing layer | End to end | Note |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| G-01 | opening_and_who_is_on_the_line | yes | structured | passes | - | 1/1 | none | 2/2 | - | encoded, enforcement not played |  |
| G-02 | opening_and_who_is_on_the_line | yes | structured | passes | - | 1/1 | 1/1 | none | - | enforced |  |
| G-03 | opening_and_who_is_on_the_line | yes | unsupported | cannot carry the rule | - | none | 0/1 | none | schema | unsupported |  |
| G-04 | opening_and_who_is_on_the_line |  | text | not evaluated | not evaluated | none | none | none | conversational | not evaluated offline |  |
| G-05 | identity | yes | structured | passes | - | 1/1 | none | none | - | encoded, enforcement not played |  |
| G-06 | identity | yes | structured | passes | - | none | 1/1 | none | - | enforced |  |
| G-07 | identity | yes | structured | passes | - | 1/1 | 1/1 | none | - | enforced |  |
| G-08 | identity |  | text | not evaluated | not evaluated | none | none | none | conversational | not evaluated offline |  |
| G-09 | the_products | yes | structured | passes | - | 3/3 | none | none | - | encoded, enforcement not played |  |
| G-10 | dates | yes | structured | passes | - | 2/2 | none | none | - | encoded, enforcement not played |  |
| G-11 | dates | yes | structured | passes | - | 3/3 | none | none | - | encoded, enforcement not played |  |
| G-12 | dates | yes | structured | passes | - | 2/2 | none | none | - | encoded, enforcement not played |  |
| G-13 | dates | yes | structured | passes | - | none | 1/1 | none | - | enforced |  |
| G-14 | dates | yes | unsupported | cannot carry the rule | - | none | 0/1 | none | schema | unsupported |  |
| G-15 | amounts | yes | structured | passes | - | 1/1 | none | none | - | encoded, enforcement not played |  |
| G-16 | amounts | yes | structured | passes | - | 5/5 | 1/1 | none | - | enforced |  |
| G-17 | amounts |  | text | not evaluated | not evaluated | none | none | none | conversational | not evaluated offline |  |
| G-18 | recording | yes | partial | passes | - | none | 1/1 | 2/2 | - | partial | not confirmed by behaviour offline: Whether the customer said yes before create_promise is called can only be seen in a live call. Offline it is shown only that the read-back question is in the spec and in the prompt of the step in which create_promise exists. That a second promise for a product is not recorded is enforced and checked. |
| G-19 | recording | yes | structured | passes | - | 1/1 | 1/1 | none | - | enforced |  |
| G-20 | recording |  | text | passes (text only, weak evidence) | - | none | none | 1/1 | - | text only |  |
| G-21 | notes | yes | structured | passes | - | 2/2 | 1/1 | none | - | enforced |  |
| G-22 | handing_the_call_over |  | text | passes (text only, weak evidence) | - | none | none | 1/1 | - | text only |  |
| G-23 | handing_the_call_over | yes | partial | passes | - | 1/1 | 1/1 | none | - | partial |  |
| G-24 | closing | yes | structured | passes | - | 2/2 | 3/3 | none | - | enforced |  |

Failed checks:

- G.c6 (calls; G-14): compiled level: step 4 (create_promise): expected blocked, got ok / compiled level: step 5 (create_promise): expected ok, got refused / compiled level: ledger promises is [('p1', 900.0, '2026-10-15'), ('p2', 800.0, '2026-10-16')], expected [('p1', 900.0, '2026-10-15'), ('p2', 800.0, '2026-10-15')]
- G.c7 (calls; G-03): compiled level: step 1 (schedule_callback): expected blocked, got ok / compiled level: step 3 (schedule_callback): expected ok, got detached / compiled level: ledger callbacks is [('2026-10-15', '12:45')], expected [('2026-10-15', '13:31')]

## Mutated reference specs

Each mutation changes one thing in a reference spec. The evaluator has to flag the rules that thing belongs to. A rule that shares a failed whole-call check with one of them is flagged too and is listed as coupled.

| Package | Mutation | Expected | Also flagged (coupled) | Result |
| --- | --- | --- | --- | --- |
| c | store card floor 40 -> 45 percent | C-12 | - | ok |
| c | first deadline band 5 -> 6 calendar days | C-10 | C-09 | ok |
| c | wrong answers allowed 2 -> 3 | C-05 | - | ok |
| c | outcome table loses the reason wrong_number | C-20 | - | ok |
| d | credit card floor 30 -> 35 percent | D-07 | - | ok |
| d | mortgage deadline 3 -> 4 business days (the check that a date after the deadline is refused moves with it) | D-09, D-10 | - | ok |
| d | callback window ends 17:00 -> 18:00 | D-02 | - | ok |
| d | promise attempts 3 -> 2 | D-13 | - | ok |
| e | electricity floor 35.00 -> 40.00 | E-11 | E-12 | ok |
| e | the vulnerable floor is dropped | E-12 | E-11 | ok |
| e | ten calendar days -> twelve (the boundary check of rule 9 expects the date of rule 8) | E-08, E-09 | E-07 | ok |
| e | Friday becomes a business day (every date rule counts business days) | E-05, E-06, E-07, E-08, E-09 | - | ok |
| f | memo ignored: card minimum back to half | F-17 | - | ok |
| f | memo ignored: latest date back to 10 calendar days | F-13 | - | ok |
| f | memo ignored: three wrong answers (the unverified close is played after two) | F-08, F-37 | - | ok |
| f | memo ignored: 16 October is not a holiday (the dates that count working days move with it) | F-12, F-13, F-14 | - | ok |
| f | memo ignored: notes after a handover | F-28 | - | ok |
| f | memo ignored: the mail's callback window | F-04 | - | ok |
| f | the payment plans team closes with the wrong code | F-32 | - | ok |
| f | a promise comes before a handover at the close | F-30 | - | ok |
| f | the questions are asked in the old order | F-06, F-53 | - | ok |
| g | dates in numbers read as day, month, year | G-06 | - | ok |
| g | card floor 25.00 -> 20.00 | G-16 | - | ok |
| g | Saturday is not a business day (the dates that depend on the week move with it) | G-10, G-11, G-13 | - | ok |
| g | a promise comes before a transfer at the close | G-24 | - | ok |
| g | callbacks on the next two business days only | G-02 | - | ok |

