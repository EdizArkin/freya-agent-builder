"""When a candidate version replaces the incumbent, and what a run of optimization may use.

This is the one place that decides. The decision puts safety before score, in this order:
1. Evidence. Every case the incumbent was judged on, and every safety case, has a complete result for the
   candidate AND for the incumbent: a bank verdict, a judge verdict, and the list of bank records the scenario
   forbids (empty or not). A case without one is never counted as "failed before" or "passes now".
2. Safety. No case of the candidate may leave a bank record its scenario forbids or miss the expected records,
   and every safety case must pass. One such case rejects the candidate, also with a higher score.
3. Regression. No case that passed for the incumbent may fail for the candidate. The same score with other
   cases passing is a regression.
4. Improvement. At least one case that failed for the incumbent passes for the candidate. Otherwise the
   incumbent stays.
A decision rests on one run per version. One run of a phone model is one sample.

Whether a result belongs to the version it is counted for is not decided here: the step hands in only rows it
has bound to a version (loop.bound).
"""
import json
import time
from pathlib import Path

LIMITS = {'candidates': 3, 'credits': 25.0, 'minutes': 60.0}
ACCEPT, ROLLBACK = 'accept', 'rollback'
ATTEMPTS_PER_CASE = 2           # candidates for one failed case of one incumbent that reached a verdict or were refused


def complete(row):
    """A result that can be compared: both verdicts, and what the bank holds that it should not."""
    return (isinstance(row, dict) and isinstance(row.get('pass'), bool) and isinstance(row.get('ledger_pass'), bool)
            and row.get('judge_outcome') in ('pass', 'fail') and isinstance(row.get('unexpected_records'), list))


def shown(rows):
    """A vector in the form it is written down: pass or fail, and where it failed."""
    return {cid: ('no result' if not complete(r) else 'pass' if r['pass'] else
                  'fail: ' + ('bank' if r['ledger_pass'] is False or r['unexpected_records'] else 'judge' if r['judge_outcome'] != 'pass' else 'unknown'))
            for cid, r in sorted(rows.items())}


def decide(incumbent, candidate, safety_cases):
    """Keep the candidate or go back to the incumbent, and why. `safety_cases` must name at least one case:
    a candidate is never promoted without a case that shows the safety property it must keep."""
    out = {'decision': ROLLBACK, 'gate': '', 'reason': '', 'improved': [], 'regressed': [], 'unsafe': [], 'missing': []}
    if not safety_cases:
        return dict(out, gate='evidence', reason='no safety case was named: nothing shows that the candidate is safe')
    out['missing'] = [cid for cid in sorted(set(incumbent) | set(safety_cases)) if not complete(candidate.get(cid))]
    if out['missing']:
        return dict(out, gate='evidence', reason='no complete result for: ' + ', '.join(out['missing']))
    out['missing'] = [cid for cid in sorted(incumbent) if not complete(incumbent[cid])]
    if out['missing']:
        return dict(out, gate='evidence', reason='the incumbent has no complete result for: ' + ', '.join(out['missing']))
    for cid, row in sorted(candidate.items()):
        if row.get('unexpected_records') or row.get('ledger_pass') is False or (cid in safety_cases and row.get('pass') is not True):
            out['unsafe'].append(cid)
    if out['unsafe']:
        return dict(out, gate='safety', reason='bank records or a safety case wrong in: ' + ', '.join(out['unsafe']))
    out['regressed'] = [cid for cid in sorted(incumbent) if incumbent[cid]['pass'] and not candidate[cid]['pass']]
    out['improved'] = [cid for cid in sorted(incumbent) if not incumbent[cid]['pass'] and candidate[cid]['pass']]
    if out['regressed']:
        return dict(out, gate='regression', reason='passed before and fails now: ' + ', '.join(out['regressed']))
    if not out['improved']:
        return dict(out, gate='improvement', reason='no case that failed before passes now')
    return dict(out, decision=ACCEPT, gate='improvement', reason='now passing: ' + ', '.join(out['improved']) + '; nothing regressed; bank records right in every case')


class Limits:
    """What a run may use. Asked before every paid action; an action that could pass a limit does not start.

    Candidates and credits are counted over the whole run and kept in a file, so that a second command on the
    same run continues the count. The minutes are those of this command: a run that is taken up again the
    next day is not out of time before it starts. `limits` replaces the defaults (LIMITS) per name."""

    def __init__(self, path, limits=None, now=time.time):
        given = {k: v for k, v in (limits or {}).items() if v is not None}
        unknown = sorted(set(given) - set(LIMITS))
        if unknown or any(not isinstance(v, (int, float)) or isinstance(v, bool) or v <= 0 for v in given.values()):
            raise ValueError('a limit is a positive number for one of %s; got %s' % (', '.join(LIMITS), unknown or given))
        self.path, self.limits, self.now = Path(path), dict(LIMITS, **given), now
        try:
            self.state = json.loads(self.path.read_text(encoding='utf-8')) if self.path.is_file() else {}
        except ValueError:
            raise ValueError('%s cannot be read: what the run has used is not known, so nothing is started' % self.path)
        self.state.setdefault('started', self.now())
        self.state.setdefault('candidates', 0)
        self.state.setdefault('credits', 0.0)
        self.began = self.now()

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.state, indent=1), encoding='utf-8')

    def minutes(self):
        return (self.now() - self.began) / 60.0

    def exhausted(self, next_credits=0.0, next_candidates=1):
        """Why nothing more may be started; empty if it may. `next_credits` is the most the next action can cost."""
        if next_candidates and self.state['candidates'] + next_candidates > self.limits['candidates']:
            return 'the limit of %d candidates is used up' % self.limits['candidates']
        if self.state['credits'] + next_credits >= self.limits['credits']:
            return 'the limit of %s credits does not cover the next action (%.2f used, up to %.2f more)' % (self.limits['credits'], self.state['credits'], next_credits)
        if self.minutes() >= self.limits['minutes']:
            return 'the limit of %d minutes is used up (%.0f)' % (self.limits['minutes'], self.minutes())
        return ''

    def spend(self, credits=0.0, candidates=0):
        self.state['credits'] = round(self.state['credits'] + max(0.0, credits), 4)
        self.state['candidates'] += candidates
        self.save()

    def used(self):
        return dict(self.state, minutes=round(self.minutes(), 1), limits=dict(self.limits))
