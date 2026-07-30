# Sealed Holdout metadata

This directory contains qualification metadata only.

- `manifests/`: two `SEALED` manifests with zero live calls.
- `approval/`: `PENDING_APPROVAL` material with no approver or decision.
- `reports/`: safe distributions and zero-finding duplicate summaries.
- `*_scorer_v1.json`: frozen metric names and directions.
- `*_gate_v1.json`: frozen thresholds.

Private datasets, conversations and Golden labels are stored outside the Git
worktree under `F:\agent\fixflow-holdouts`. Do not copy those files into this
directory or any Docker build context.
