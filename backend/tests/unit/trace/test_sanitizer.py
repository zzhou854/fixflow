import pytest
from app.trace.models import TracePayload
from app.trace.sanitizer import TraceSanitizer, UnsafeTracePayload


def test_trace_sanitizer_truncates_approved_strings() -> None:
    sanitizer = TraceSanitizer(max_payload_bytes=512, max_string_length=16)
    assert sanitizer.sanitize(TracePayload(summary="x" * 40))["summary"] == "x" * 16


@pytest.mark.parametrize(
    "payload",
    [
        {"authorization": "Bearer secret"},
        {"nested": {"password_hash": "hash"}},
        {"items": [{"api_key": "secret"}]},
        {"nested": {"idempotency_key": "raw-http-key"}},
        {"checkpoint": {"conversation_messages": ["private text"]}},
        {"failure": {"traceback": "internal stack"}},
    ],
)
def test_trace_sanitizer_recursively_rejects_sensitive_keys(payload: object) -> None:
    sanitizer = TraceSanitizer(max_payload_bytes=512, max_string_length=64)
    with pytest.raises(UnsafeTracePayload):
        sanitizer.inspect_untrusted(payload)


def test_trace_payload_rejects_arbitrary_fields() -> None:
    with pytest.raises(ValueError):
        TracePayload.model_validate({"sql": "select * from users"})
