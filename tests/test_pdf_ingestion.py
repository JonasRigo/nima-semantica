import base64
import hashlib
import importlib.abc
import subprocess
import sys

import pytest
from nima_semantica.pdf.assets import verify_assets
from nima_semantica.pdf.support import PdfError
from nima_semantica.corpus import prepare_source
from nima_semantica.models import NimaError


def test_owned_model_assets_reject_corruption_and_escaping_symlinks(tmp_path):
    root = tmp_path / "models"
    root.mkdir()
    model = root / "model"
    model.write_bytes(b"approved")
    manifest = {"approved_artifacts": [{"path": "model", "size": 8, "sha256": hashlib.sha256(b"approved").hexdigest()}]}
    assert verify_assets(root, manifest, check_versions=False)["manifest_hash"]
    model.write_bytes(b"tampered")
    with pytest.raises(PdfError, match="integrity"):
        verify_assets(root, manifest, check_versions=False)
    model.unlink()
    external = tmp_path / "external"
    external.write_bytes(b"approved")
    model.symlink_to(external)
    with pytest.raises(PdfError, match="escapes"):
        verify_assets(root, manifest, check_versions=False)


@pytest.mark.parametrize("corrupt", [False, True])
def test_pdf_artifacts_become_owned_canonical_evidence(store, corrupt):
    class Provider:
        def normalize_pdf(self, profile, data):
            return "A source equation: $x=x$.", [{"kind": "parser_manifest", "provenance": [{"page": 1}],
                "artifact_bundle": [{"sha256": "0" * 64 if corrupt else hashlib.sha256(b"crop").hexdigest(),
                    "data_base64": base64.b64encode(b"crop").decode()}]}]
    source = {"artifact_id": store.artifact(b"%PDF-fixture"), "name": "paper.pdf"}
    if corrupt:
        with pytest.raises(NimaError, match="integrity"):
            prepare_source(store, Provider(), "test", source, "pdf-test")
    else:
        result = prepare_source(store, Provider(), "test", source, "pdf-test")
        diagnostic = result[1].content["diagnostics"][0]
        assert "artifact_bundle" not in diagnostic
        assert store.read_artifact(diagnostic["artifact_ids"][0]) == b"crop"


def test_pdf_has_no_nima_agi_or_semantica_import_dependency():
    code = """
import importlib.abc, sys
class Guard(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name.split('.')[0] in ('nima', 'semantica'):
            raise AssertionError('forbidden dependency: '+name)
sys.meta_path.insert(0, Guard())
from nima_semantica.pdf import worker, assets, _ported
from nima_semantica.orchestration.langflow.pdf_client import PdfNormalizerClient
"""
    subprocess.run([sys.executable, "-c", code], check=True, capture_output=True, text=True, timeout=15)


def test_pdf_service_auth_and_header_checks(tmp_path):
    from fastapi.testclient import TestClient
    from nima_semantica.pdf.server import create_app
    models = tmp_path / "models"
    models.mkdir()
    with TestClient(create_app(models, tmp_path / "data", "x" * 32)) as client:
        assert client.get("/health").json()["service"] == "nima-semantica-pdf"
        assert client.post("/normalize", content=b"%PDF-test").status_code == 401
        assert client.post("/normalize", content=b"not a PDF", headers={"authorization": "Bearer " + "x" * 32}).status_code == 422


def test_negation_repair_closes_pdfium_resources_without_context_manager(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from nima_semantica.pdf._ported import _remove_redundant_or_repair_orphan_negations
    closed = []
    class TextPage:
        def get_text_range(self):
            return "x ̸= y"
        def get_charbox(self, index):
            return (9, 4, 11, 6)
        def close(self):
            closed.append("text")
    class Page:
        def get_textpage(self):
            return TextPage()
        def close(self):
            closed.append("page")
    class Document:
        def __getitem__(self, index):
            return Page()
        def close(self):
            closed.append("document")
    monkeypatch.setitem(sys.modules, "pypdfium2", SimpleNamespace(PdfDocument=lambda path: Document()))
    box = SimpleNamespace(l=10, r=11, b=0, t=10, model_dump=lambda **kwargs: {"l": 10, "r": 11, "b": 0, "t": 10})
    glyph = SimpleNamespace(label="text", text="̸", self_ref="glyph", prov=[SimpleNamespace(page_no=1, bbox=box)])
    text = SimpleNamespace(label="text", text="x = y", self_ref="text", prov=[SimpleNamespace(page_no=1, bbox=SimpleNamespace(l=0, r=30, b=0, t=10))])
    result = _remove_redundant_or_repair_orphan_negations(SimpleNamespace(texts=[glyph, text]), "formula", tmp_path / "source.pdf")
    assert text.text == "x ≠ y"
    assert glyph.text == ""
    assert result[0]["kind"] == "joined_native_text_negation_overlay"
    assert closed == ["text", "page", "document"]
