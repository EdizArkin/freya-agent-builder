# M15 closure: schema generalisation and extraction reliability

11 October 2026. Extraction model `claude-sonnet-5-5`, effort high, adaptive thinking, 32,000 output tokens at most, no server-side model fallback. All packages are synthetic and were written by the author of the builder; none is independently validated. Every figure below is from one run per configuration unless it says otherwise.

## Result in short

| Evidence | Package | Configuration | Extraction score | Critical-rule recall | Incorrect-policy rate |
| --- | --- | --- | --- | --- | --- |
| Development | C, D, E | single, audit, double | 1.000 each | 1.000 | 0 |
| Model selection | F | single | 0.971 | 0.941 | 0 |
| Model selection | F | audit | 1.000 | 1.000 | 0 |
| Model selection | F | double | 1.000 | 1.000 | 0 |
| **Final holdout** | **G** | **audit (selected)** | **1.000** | **1.000** | **0** |

The selected configuration is the audit strategy with the original extraction prompt. On G it produced a valid spec without a repair turn, encoded all 19 rules that can be judged offline, and reported both rules the schema cannot carry.

The score measures extraction: whether each rule of a brief is in the spec in the form the schema has for it, and whether a rule the schema cannot carry is reported. It does not say that the compiled agent enforces the rule, and it says nothing about what a phone model does in a call.

## Development: C, D, E

Judged against reference version 2 (`benchmarks/references/v2/`), which encodes the nine rules that agent-spec/1 can carry since phase 3. The frozen packages are unchanged.

| Strategy | Score, each package | Calls per package | Mean cost per package | Mean latency |
| --- | --- | --- | --- | --- |
| single | 1.000 | 1 | $0.095 | 51 s |
| audit | 1.000 | 2 | $0.118 | 60 s |
| double | 1.000 | 2 to 4 | $0.233 | 119 s |

The single strategy was run 28 times in all on these packages (3 as baseline, 25 inside the GEPA run): 28 scored 1.000, 27 were valid on the first reply and one needed one repair turn.

These three briefs are the ones the phase 3 schema fields and the prompt wording were written against. A tie at the ceiling here is not evidence that the builder reads an unfamiliar brief.

### What two extractions of one brief disagree on

The double strategy found four high-risk items on which two extractions of package C differed. The two specs themselves were not saved, so the items were reconstructed offline by comparing all ten saved extractions of C. Three kinds of difference appear:

| Difference | Finding | Consequence |
| --- | --- | --- |
| "Promise allowed" for the sample product | Not a difference of policy. Validation sampled the true/false field `plan_active` as the text `1234`, so two equivalent ways of writing the eligibility rule gave different answers for a product no bank returns | A defect of the builder's sampling. Fixed before the candidates were frozen: a true/false field is sampled as true and false. With the fix these items no longer differ |
| Deadline for a product that is 0 days late | Real difference: fifth or second calendar day. Brief C defines the deadline for 1 to 30 days and for more, and says nothing about 0 | The source is silent. No check asserts it, correctly. A product 0 days late is not in arrears |
| Order of the forced close: promise first, or transfer first | Real difference: after a recorded promise and a later transfer the call closes as `promise` or as `transferred`. Brief C gives no order | The source is silent. The key has no check that plays a promise and then a transfer, so both score the same. Extractions vary on it (3 of 8 single runs put the promise first, 5 the transfer) |

None of the three is a scoring error. The second and third are points where a brief leaves a choice open and the extractor makes it differently from run to run without saying so. The double strategy now writes every differing item and every verdict to its trace.

## GEPA

One run on C, D and E only: 25 metric evaluations, $2.59, 26 model calls, inside the 60-minute limit. The optimiser could change the three sections `reading`, `rules_writing` and `structuring` and nothing else.

GEPA rewrites a section only after an evaluation that scores below perfect. All 25 scored 1.000, so no reflection call was made and no candidate was proposed. The run measured the stability of the current prompt and nothing more. The original prompt is retained; there was no other candidate to consider.

## Selection on F

One batch, all three candidates frozen beforehand (`m15.candidates.json`, commit `cc2b688`), logged before the first call, evaluated only after every extraction was finished.

| Candidate | Valid | Score | Critical recall | Judged rules correct | Unsupported rules reported | Calls | Tokens in / out | Latency | Cost |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| single | yes | 0.971 | 0.941 | 34 of 34 | 0 of 2 | 1 | 9,732 / 9,159 | 62 s | $0.111 |
| audit | yes, after 1 repair turn | 1.000 | 1.000 | 34 of 34 | 2 of 2 | 3 | 34,227 / 13,874 | 87 s | $0.207 |
| double | yes | 1.000 | 1.000 | 34 of 34 | 2 of 2 | 3 | 24,587 / 18,079 | 121 s | $0.230 |

F is raw bank material in four documents with an amending memo and renamed API fields. All three candidates encoded every judged rule correctly, including the renamed fields.

The only difference is two critical rules the schema cannot carry (a promise for today only for the full amount; amounts in multiples of ten). The single extraction wrote them into the rule text and did not report them as unsupported. The other two reported them.

By the rule fixed before F (validity, critical recall, score, incorrect-policy rate, then cost and latency), audit and double are of equal quality and audit is cheaper and faster. **Audit is selected** (`m15.selection.json`, committed before G was opened).

What this selection is worth: on F the audit step itself found nothing. The audit candidate scored higher because its own first extraction happened to report the two rules. That is one sample per candidate of a difference between independent extractions with the same prompt. It does not show that auditing causes better extraction. These F numbers chose the configuration and are model-selection evidence, not a test result.

## Final holdout: G

One run of the selected candidate, at commit `4ba1135`. Nothing was changed after it and nothing was decided from it.

| | |
| --- | --- |
| Valid spec | yes, no repair turn |
| Extraction score / critical recall / incorrect-policy rate | 1.000 / 1.000 / 0 |
| Rules judged offline | 19 of 19 correct |
| Rules the schema cannot carry | 2 of 2 reported (G-03, G-14) |
| Rules not evaluated offline (conversational) | 3 |
| Checks through the compiled workflow | 27 of 29 pass; the 2 that fail belong to the two unsupported rules |
| Tool API contract problems | none |
| Calls, tokens in / out | 3; 29,960 / 16,149 |
| Latency, cost | 98 s, $0.221 |

On G the audit step did something: it found that the first extraction had not reported the rule that all promises of one call carry the same date, quoted the sentence from the brief, and the revision reported it.

Limits of this number: one package, one run, of the clean form only (the raw-material form was used for selection). It is the only figure in this report that influenced nothing, and it is a thin estimate.

## Where the limits are, by layer

| Layer | State after M15 |
| --- | --- |
| Extraction | No rule the schema can carry was extracted wrongly on any package (C, D, E, F, G). The observed weaknesses: unsupported rules are not reported every time (2 of the 5 first extractions on F and G left at least one unreported; on G the audit step caught it), and where a brief is silent the extractor chooses differently from run to run without saying so |
| Schema | agent-spec/1 cannot carry four holdout rules (F-18, F-19, G-03, G-14). D-11 (household minimum) and E-18 (promise after a transfer) are carried in part. A rule that is only reported is not enforced: the compiled agent records the promise or books the callback those rules forbid |
| Compiler and runtime | No rule that a spec encodes failed in the compiled workflow on any package. Tool API contract checks found nothing. Renamed call and success fields (package F) work |
| Tool API | No cancel operation: the all-or-none part of D-11 cannot be enforced |
| Live platform | Not measured in phases 4 and 5. Known from phase 3: a pre-hook the platform does not run cannot block; a blocked request reaches the phone model through the live state, not as the tool's answer, and the model can take it for a service failure; shared-hook concurrency (M14.3) is unresolved |
| Conversation | 32 rules across the five packages are conversational and have no offline check |

## Cost

| Step | Calls | Cost |
| --- | --- | --- |
| Baseline, single on C, D, E | 3 | $0.29 |
| Audit and double on C, D, E | 15 | $1.05 |
| GEPA, 25 evaluations | 26 | $2.59 |
| Selection batch on F | 7 | $0.55 |
| Final run on G | 3 | $0.22 |
| **Total** | **54** | **$4.70** |

Phase 5 used $0.77 of its $3.00 limit. Of the $18 of credit stated before the first paid run, about $13.30 should remain; this is computed from the token counts the API returned and the published prices ($2 in, $10 out per million tokens), not read from the billing page. Every call was made only while the limit covered its worst case, and no call was charged at an assumed amount.

## Holdout integrity

- `m15.exposure.jsonl` has four entries: F started and completed (batch `113a4a9252ba`), G started and completed (batch `6346277ec30b`). No other evaluation of an extracted spec on F or G exists.
- Candidates and the selection rule were committed before F; the selection was committed before G. Both batches ran from a clean working tree and record their commit.
- No prompt, schema, validator, compiler or runtime change was made after F was opened.
- The GEPA run opened the documents and keys of C, D and E only.
- The package manifest and the reference manifest verify. F and G have no second reference.
- Not covered by any control: the author of the builder also wrote F and G. Their keys were not read while the phase 3 schema and the prompt were written, but that rests on procedure, not on a mechanism.

## Risks carried into M16 and M17

1. **A reported rule is not an enforced rule.** With `--best-effort` a build goes through with unsupported rules in the rule text only. On F and G that means a promise or callback the brief forbids is recorded if the phone model makes the call. The final briefs will very likely contain such rules.
2. **Unsupported rules are not always reported.** Two of the five first extractions on F and G left a critical unsupported rule unreported. The audit step caught one of the two; neither audit nor double is shown to remove the risk.
3. **Silent choices where a brief is silent.** Close precedence and boundary cases vary between runs. Nothing warns the user.
4. **Thin evidence for the selected strategy.** One run per candidate. On F audit cost $0.21 against $0.11 for single and took 87 s against 62 s; the 30-minute build limit is not at risk.
5. **First replies that fail validation.** Two of 42 extractions needed one repair turn: one reply was JSON that did not parse, one wrote `promise.resolve_first` in the wrong shape. The repair loop handled both.
6. **Refusal channel.** Blocked calls are still read by the phone model as failures in some conversations (phase 3 finding, documented for M16).
7. **Platform limits unchanged:** pre-hooks that do not run, shared-hook concurrency.
8. **The digest covers high-risk content only.** A difference between two extractions outside it is not seen by audit or double.
