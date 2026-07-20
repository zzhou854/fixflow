"""Strict policy import, retrieval, and evidence contracts."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.domain.enums import IssueCategory
from app.policy.constants import (
    DEFAULT_MINIMUM_FUSION_SCORE,
    DEFAULT_MINIMUM_VECTOR_SIMILARITY,
    MAX_POLICY_BATCH_CHARACTERS,
    MAX_POLICY_CHUNK_LENGTH,
    MAX_POLICY_CHUNKS_PER_DOCUMENT,
    MAX_POLICY_EVIDENCE_EXCERPT,
    MAX_POLICY_QUERY_LENGTH,
)
from app.policy.enums import EvidenceSufficiency, PolicyTopic

ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
LongText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_POLICY_CHUNK_LENGTH),
]
DecisionText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)
]


def require_aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value


class PolicyModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PolicyChunkInput(PolicyModel):
    content: LongText
    search_terms: tuple[ShortText, ...] = Field(min_length=1, max_length=30)
    decision_key: DecisionText | None = None
    decision_value: DecisionText | None = None

    @field_validator("search_terms", mode="after")
    @classmethod
    def normalize_search_terms(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        from app.policy.normalization import normalize_policy_text

        normalized = tuple(dict.fromkeys(normalize_policy_text(value) for value in values))
        if not normalized or any(not value for value in normalized):
            raise ValueError("search_terms must contain normalized non-blank values")
        return normalized

    @model_validator(mode="after")
    def validate_decision_pair(self) -> "PolicyChunkInput":
        if (self.decision_key is None) != (self.decision_value is None):
            raise ValueError("decision_key and decision_value must both be set or both be absent")
        return self


class PolicyDocumentInput(PolicyModel):
    policy_code: Annotated[
        str, StringConstraints(strip_whitespace=True, pattern=r"^[A-Z0-9_\-]{3,64}$")
    ]
    title: ShortText
    issue_category: IssueCategory | None = None
    policy_topic: PolicyTopic
    version: int = Field(ge=1, le=1_000_000)
    authority_rank: int = Field(ge=1, le=100)
    effective_from: datetime
    effective_to: datetime | None = None
    source_name: ShortText
    source_reference: ShortText
    is_enabled: bool = True
    chunks: tuple[PolicyChunkInput, ...] = Field(
        min_length=1, max_length=MAX_POLICY_CHUNKS_PER_DOCUMENT
    )

    @model_validator(mode="after")
    def validate_document(self) -> "PolicyDocumentInput":
        require_aware(self.effective_from, "effective_from")
        if self.effective_to is not None:
            require_aware(self.effective_to, "effective_to")
            if self.effective_to <= self.effective_from:
                raise ValueError("effective_to must be after effective_from")
        total_characters = sum(len(chunk.content) for chunk in self.chunks)
        if total_characters > MAX_POLICY_BATCH_CHARACTERS:
            raise ValueError("policy document content exceeds the batch character limit")
        return self


class PolicyCorpus(PolicyModel):
    declaration: Literal["Policy corpus is synthetic demonstration data for FixFlow."]
    documents: tuple[PolicyDocumentInput, ...] = Field(min_length=1, max_length=100)


class EmbeddingProfile(PolicyModel):
    provider: ShortText
    model: ShortText
    dimension: int = Field(ge=1)
    profile_version: ShortText


class EmbeddingResult(PolicyModel):
    vector: tuple[float, ...]
    profile: EmbeddingProfile

    @model_validator(mode="after")
    def validate_vector(self) -> "EmbeddingResult":
        if len(self.vector) != self.profile.dimension:
            raise ValueError("embedding vector does not match its profile dimension")
        if not all(float("-inf") < value < float("inf") for value in self.vector):
            raise ValueError("embedding values must be finite")
        return self


class PolicyImportResult(PolicyModel):
    document_id: UUID
    content_hash: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    chunk_count: int = Field(ge=1)
    replayed: bool


class PolicyRetrievalRequest(PolicyModel):
    query_text: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_POLICY_QUERY_LENGTH),
    ]
    issue_category: IssueCategory
    policy_topics: tuple[PolicyTopic, ...] = Field(min_length=1, max_length=9)
    as_of: datetime
    intent_version: int = Field(ge=1)
    top_k: int = Field(default=5, ge=1, le=20)
    minimum_vector_similarity: float = Field(
        default=DEFAULT_MINIMUM_VECTOR_SIMILARITY, ge=-1.0, le=1.0
    )
    minimum_fusion_score: float = Field(default=DEFAULT_MINIMUM_FUSION_SCORE, ge=0.0, le=1.0)
    trace_id: UUID

    @model_validator(mode="after")
    def validate_request(self) -> "PolicyRetrievalRequest":
        require_aware(self.as_of, "as_of")
        if len(set(self.policy_topics)) != len(self.policy_topics):
            raise ValueError("policy_topics must not contain duplicates")
        return self


class PolicyCandidate(PolicyModel):
    chunk_id: UUID
    document_id: UUID
    policy_code: str
    policy_version: int
    title: str
    issue_category: IssueCategory | None
    policy_topic: PolicyTopic
    content: str
    search_terms: tuple[str, ...]
    source_name: str
    source_reference: str
    effective_from: datetime
    effective_to: datetime | None
    authority_rank: int
    decision_key: str | None
    decision_value: str | None
    vector_similarity: float


class PolicyEvidence(PolicyModel):
    evidence_id: UUID
    document_id: UUID
    policy_code: str
    policy_version: int
    title: str
    issue_category: IssueCategory | None
    policy_topic: PolicyTopic
    content_excerpt: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True, min_length=1, max_length=MAX_POLICY_EVIDENCE_EXCERPT
        ),
    ]
    source_name: str
    source_reference: str
    effective_from: datetime
    effective_to: datetime | None
    authority_rank: int
    vector_similarity: float | None = Field(default=None, ge=-1.0, le=1.0)
    vector_rank: int | None
    lexical_score: float | None = Field(default=None, ge=0.0, le=1.0)
    lexical_rank: int | None
    fusion_score: float = Field(ge=0.0, le=1.0)
    decision_key: str | None
    decision_value: str | None
    retrieved_as_of: datetime
    instruction_like_content_detected: bool = False


class PolicyConflict(PolicyModel):
    decision_key: str
    conflicting_values: tuple[str, ...]
    evidence_ids: tuple[UUID, ...]


class PolicyRetrievalResult(PolicyModel):
    evidence: tuple[PolicyEvidence, ...]
    conflicts: tuple[PolicyConflict, ...]
    sufficiency: EvidenceSufficiency
    missing_policy_topics: tuple[PolicyTopic, ...]
    retrieved_as_of: datetime
    intent_version: int = Field(ge=1)
    issue_category: IssueCategory
    requested_policy_topics: tuple[PolicyTopic, ...]
    embedding_profile: EmbeddingProfile
    policy_query_fingerprint: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class StoredPolicyIdentity(PolicyModel):
    document_id: UUID
    content_hash: str
    embedding_profile: EmbeddingProfile
