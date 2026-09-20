"""Conservative deterministic policy-evidence sufficiency rules."""

from app.policy.enums import EvidenceSufficiency, PolicyTopic
from app.policy.models import PolicyConflict, PolicyEvidence


def evaluate_sufficiency(
    *,
    evidence: tuple[PolicyEvidence, ...],
    conflicts: tuple[PolicyConflict, ...],
    required_topics: tuple[PolicyTopic, ...],
    minimum_vector_similarity: float,
    minimum_fusion_score: float,
) -> tuple[EvidenceSufficiency, tuple[PolicyTopic, ...]]:
    covered = {
        item.policy_topic
        for item in evidence
        if item.fusion_score >= minimum_fusion_score
        and (
            (
                item.vector_similarity is not None
                and item.vector_similarity >= minimum_vector_similarity
            )
            or (item.lexical_score is not None and item.lexical_score > 0.0)
        )
    }
    missing = tuple(topic for topic in required_topics if topic not in covered)
    if conflicts:
        return EvidenceSufficiency.CONFLICTING, missing
    if not evidence or missing:
        return EvidenceSufficiency.INSUFFICIENT, missing
    return EvidenceSufficiency.SUFFICIENT, ()
