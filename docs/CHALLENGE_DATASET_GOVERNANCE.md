# Challenge dataset governance

FixFlow Challenge corpora are limited formal-qualification evidence, not a
claim of production language coverage.

## Authoring and review

- Challenge authors and rule developers should be different people whenever
  staffing permits.
- Suite distribution, critical cases, and acceptance thresholds are frozen
  before the first live call.
- Golden labels require a second independent review. Ambiguous labels are
  corrected only before consumption; afterwards they remain immutable evidence
  with an explicit authoring-defect record.
- Inputs are synthetic, contain no real personal data, and are scanned for
  credentials and forbidden identifiers.
- Prompt examples, the development corpus, and all previous Challenge versions
  must have zero exact input duplicates with a new Challenge.
- Template similarity is bounded and reviewed; paraphrase-only copies are not
  sufficient independence.

## Consumption

- A Challenge version has zero model calls before a clean, content-addressed
  candidate commit passes both formal Regression repeats.
- The first live call permanently marks the version consumed.
- Repeat 2 is allowed only when Repeat 1 passes every absolute gate.
- A consumed corpus becomes historical regression evidence and is never again
  described as a holdout.
- Failure may inform a future architecture, but the same Challenge cannot be
  reused to claim blind qualification.
- Selective retries are limited to classified infrastructure failures. Model
  quality failures are never selectively rerun.

## Anti-gaming boundary

- Thresholds, difficult cases, and Golden labels are not relaxed to pass a
  candidate.
- Production code cannot branch on case IDs or contain complete Challenge
  sentences.
- FixFlow does not create an unbounded succession of holdouts. Task 17 ends
  with v7 evidence, whether qualified or blocked.
- Regression and Challenge metrics are reported separately; averages cannot
  hide failure on either corpus.

## Limits and production evidence

Sixty synthetic cases can reveal defects but cannot prove production
generalization, long-tail robustness, or demographic/language coverage.
Production activation therefore remains a separate decision requiring
de-identified real-traffic shadow evaluation, human review of failure samples,
cost and latency monitoring, drift detection, rollback criteria, and long-term
SLA evidence.
