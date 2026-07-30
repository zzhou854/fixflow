# Sealed Holdout metadata

This directory contains qualification metadata only.

- `manifests/`: immutable phase-1D-A and revised phase-1D-B `SEALED`
  manifests, all with zero live calls.
- `approval/`: historical and revised `PENDING_APPROVAL` material with no
  approver or decision.
- `reports/`: safe distributions and zero-finding duplicate summaries for
  both preparation rounds.
- `*_scorer_v1.json` / `*_gate_v1.json`: frozen 1.0.0 identities retained for
  audit evidence.
- `*_scorer_v1_1.json` / `*_gate_v1_1.json`: revised 1.1.0 field-evidence and
  Grounded hard-gate contracts.

Private datasets, conversations and Golden labels are stored outside the Git
worktree under `F:\agent\fixflow-holdouts`. Do not copy those files into this
directory or any Docker build context.

The phase-1D-A private assets have an append-only `CHANGES_REQUIRED`
disposition. The revised packages still require a fresh independent review of
all 240 cases; repository metadata must never be described as Holdout pass or
model qualification evidence.
