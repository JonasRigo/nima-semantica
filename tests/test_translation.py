import asyncio
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from nima_semantica.models import NimaError
from nima_semantica.providers import Invocation, ModelManifest
from nima_semantica.translation import LatexToLeanRequest, translate_latex, translation_result


MANIFEST = ModelManifest(provider="ollama", model="test", revision="test-digest",
    parameters={"max_tokens": 4096, "format": "json_schema"})
DRAFT = {"lean_source": "import Std\ntheorem translated (n : Nat) : n = n := by rfl\n",
    "target_declarations": ["translated"], "assumptions": [], "unresolved_gaps": [],
    "correspondence_notes": ["Natural-number domain follows the supplied LaTeX."]}


def invocation(**changes):
    return Invocation(result=DRAFT, manifest=MANIFEST, input_tokens=50, output_tokens=40, **changes)


def test_standalone_api_requires_no_research_state():
    request = LatexToLeanRequest(latex=r"For every $n\in\mathbb{N}$, $n=n$.")
    class Provider:
        def invoke(self, profile, component, payload, schema, allowance):
            assert (profile, component, allowance) == ("local", "LatexToLean", 4096)
            assert payload["latex"] == request.latex
            assert "lean_source" in schema["properties"]
            return invocation()
    result = translate_latex(request, Provider(), profile="local")
    assert result.status == "unverified_draft"
    assert result.correspondence_verified is False
    assert result.draft.lean_source == DRAFT["lean_source"]
    assert result.invocation.manifest.revision == "test-digest"
    assert len(result.request_id) == 64


@pytest.mark.parametrize("error", ["truncated", "invalid_json"])
def test_failed_completion_is_preserved(error):
    result = translation_result(LatexToLeanRequest(latex="x=x"),
        invocation(error=error, raw_output="partial completion", finish_reason="length"))
    assert result.status == "failed" and result.draft is None
    assert result.invocation.raw_output == "partial completion"


def test_invalid_schema_cannot_claim_verification():
    receipt = invocation().model_copy(update={"result": {**DRAFT, "verified": True}})
    result = translation_result(LatexToLeanRequest(latex="x=x"), receipt)
    assert result.status == "failed"
    assert "invalid translation schema" in result.diagnostics[0]


def test_generated_code_is_inert_even_when_unsafe():
    # This is NOT a Lean sandbox or static safety checker. No source is executed.
    source = '#eval IO.println "untrusted"\naxiom target : False'
    receipt = invocation().model_copy(update={"result": {**DRAFT, "lean_source": source}})
    result = translation_result(LatexToLeanRequest(latex="bad input"), receipt)
    assert result.status == "unverified_draft"
    assert result.draft.lean_source == source
    assert result.correspondence_verified is False


def test_output_allowance_and_empty_input():
    result = translation_result(LatexToLeanRequest(latex="x", max_output_tokens=1), invocation())
    assert result.status == "failed"
    with pytest.raises(ValueError):
        LatexToLeanRequest(latex="")
