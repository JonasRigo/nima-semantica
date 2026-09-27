"""Regression cases from the frozen three-paper diagnostic run."""
import base64
import copy
import json

import httpx
import pytest

from nima_semantica.models import NimaError
from nima_semantica.providers import model_json_object, strict_json_object
from nima_semantica.translation import LeanDraft


@pytest.mark.parametrize("text", ['{"ok":true}', '```json\n{"ok":true}\n```', '```\n{"ok":true}\n```'])
def test_complete_model_envelopes(text):
    assert model_json_object(text) == {"ok": True}


@pytest.mark.parametrize("text", ['before\n```json\n{}\n```', '```json\n{}',
    '```json\n{}\n```\nafter', '{} {}', '```json\n{"a":1,"a":2}\n```',
    '```json\n{"a":NaN}\n```', '```json\n{}\n```\n```json\n{}\n```'])
def test_no_json_extraction_or_repair(text):
    with pytest.raises(ValueError):
        model_json_object(text)


def test_transport_does_not_accept_model_fences():
    with pytest.raises(ValueError):
        strict_json_object('```json\n{}\n```')


def prepared(store):
    request = copy.deepcopy(examples()["Analyze Graph"])
    text = request["source"]["regions"][0]["text"]
    # A nonzero offset is essential: the original bug re-normalized this chunk.
    full = "Header.\n" + text
    scope = {"corpus_id": "fixing", "project_id": "fixing"}
    result = StoreSourceChunks({**scope, "name": "paper.txt", "text": full,
        "data_base64": base64.b64encode(full.encode()).decode(), "chunks": [
            {"text": "Header.\n", "start": 0, "end": 8, "ordinal": 0},
            {"text": text, "start": 8, "end": len(full), "ordinal": 1}]}, store)
    return scope, request, result["regions"][1]


def test_lean_heading_rejected_before_compilation():
    with pytest.raises(ValueError, match="identifiers"):
        LeanDraft(lean_source="theorem target : True := by trivial",
            target_declarations=["Theorem 4.6 (RH for Simple Nontrivial Zeros)"],
            assumptions=[], unresolved_gaps=[], correspondence_notes=[])
