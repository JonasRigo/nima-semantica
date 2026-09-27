import pytest
from nima_semantica.providers import ModelManifest, completion_envelope, Invocation


@pytest.mark.parametrize("content,reason,error", [
    ('{"ok":true}', "stop", None),
    ('{"ok":true}', "length", "truncated"),
    ('{"ok":', "length", "truncated"),
    ('{"ok":', "stop", "invalid_json"),
    ('[]', "stop", "invalid_json"),
    ('{"ok":true,"ok":false}', "stop", "invalid_json"),
    ('{"ok":NaN}', "stop", "invalid_json"),
    ('{"ok":Infinity}', "stop", "invalid_json"),
])
def test_unusable_completions_are_evidence_not_results(content, reason, error):
    manifest = ModelManifest(provider="test", model="test", revision="test", parameters={})
    result = Invocation.model_validate(completion_envelope(content, manifest,
        {"input_tokens": 100, "output_tokens": 20}, {"done_reason": reason}))
    assert result.error == error
    assert result.result == ({} if error else {"ok": True})
    assert result.raw_output == content
    assert result.input_tokens == 100
    assert result.output_tokens == 20


def test_completion_envelope_preserves_explicit_provider_usage_only():
    manifest=ModelManifest(provider="test",model="test",revision="test",parameters={})
    result=Invocation.model_validate(completion_envelope('{"ok":true}',manifest,{"input_tokens":2,"output_tokens":3},
        {"finish_reason":"tool_calls"},provider_usage={"prompt_tokens":2,"completion_tokens":3,"cost":0.0007,
            "prompt_tokens_details":{"cached_tokens":1}}))
    assert result.provider_usage=={"prompt_tokens":2,"completion_tokens":3,"cost":0.0007,
        "prompt_tokens_details":{"cached_tokens":1}}



def test_extraction_schema_only_permits_proposed_claims():
    from pydantic import ValidationError
    from nima_semantica.models import AuditOutput
    schema = AuditOutput.model_json_schema()
    assert schema["$defs"]["ExtractedClaim"]["properties"]["status"]["const"] == "proposed"
    with pytest.raises(ValidationError):
        AuditOutput.model_validate({"claims": [{"statement": "unverified", "status": "conditionally_established"}], "gaps": []})
