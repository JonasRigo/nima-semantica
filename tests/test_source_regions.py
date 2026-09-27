from types import SimpleNamespace

import pytest

from nima_semantica.corpus import regions
from nima_semantica.models import NimaError


@pytest.mark.parametrize("text", [
    "## References\n\n- [9] First reference.\n\n- [10] Second reference.\n",
    "  A paragraph with\n  indentation and   spacing.\n\nAnother paragraph.  \n",
    "# Formula\n\n$$\na + b = c\n$$\n\nRepeated words.\n\nRepeated words.\n",
])
def test_regions_preserve_complete_original_source(text):
    records = regions(text, "artifact", "document", "corpus")
    assert "".join(r.content["text"] for r in records) == text
    cursor = 0
    for record in records:
        part = record.content
        assert part["start"] == cursor
        assert text[part["start"]:part["end"]] == part["text"]
        cursor = part["end"]
    assert cursor == len(text)


@pytest.mark.parametrize("emitted", ["alpha gamma", "alpha beta changed", "alpha beta"])
def test_chunk_content_changes_and_omissions_are_rejected(monkeypatch, emitted):
    import semantica.split
    monkeypatch.setattr(semantica.split.StructuralChunker, "chunk", lambda self, text: [SimpleNamespace(text=emitted)])
    with pytest.raises(NimaError):
        regions("alpha beta gamma", "artifact", "document", "corpus")
