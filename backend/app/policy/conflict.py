"""Deterministic conflict detection over already filtered evidence."""

from collections import defaultdict

from app.policy.models import PolicyConflict, PolicyEvidence
from app.policy.normalization import normalize_decision_value, normalize_policy_text


def detect_policy_conflicts(evidence: tuple[PolicyEvidence, ...]) -> tuple[PolicyConflict, ...]:
    grouped: dict[str, dict[str, list[PolicyEvidence]]] = defaultdict(lambda: defaultdict(list))
    display_keys: dict[str, str] = {}
    for item in evidence:
        if item.decision_key is None or item.decision_value is None:
            continue
        key = normalize_policy_text(item.decision_key)
        value = normalize_decision_value(item.decision_value)
        display_keys.setdefault(key, item.decision_key.strip())
        grouped[key][value].append(item)

    conflicts: list[PolicyConflict] = []
    for key in sorted(grouped):
        values = grouped[key]
        if len(values) < 2:
            continue
        evidence_ids = tuple(
            sorted(
                (item.evidence_id for items in values.values() for item in items),
                key=str,
            )
        )
        conflicts.append(
            PolicyConflict(
                decision_key=display_keys[key],
                conflicting_values=tuple(sorted(values)),
                evidence_ids=evidence_ids,
            )
        )
    return tuple(conflicts)
