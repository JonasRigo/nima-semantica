"""Long fast reads retain all pages, resource bounds and ingestion replay."""
from contextlib import closing
from io import BytesIO
import json
import subprocess
import sys

import pytest

from nima_semantica import deep_research_fast, deep_research_fast_pdf
from nima_semantica.ingestion_diagnostics import preparation_diagnostic


def read_text(monkeypatch, reader, text):
    if reader == "text":
        return deep_research_fast.fast_text(text.encode(), "book.txt")
    monkeypatch.setattr(sys, "platform", "darwin" if reader == "macos" else "linux")
    if reader == "macos":
        monkeypatch.setattr(deep_research_fast_pdf, "extract_macos", lambda data: text.encode())
    else:
        def run(command, **kwargs):
            if reader == "pypdf" and command[0] == "pdftotext":
                raise FileNotFoundError("pdftotext unavailable")
            return subprocess.CompletedProcess(command, 0, stdout=text.encode(), stderr=b"")
        monkeypatch.setattr(subprocess, "run", run)
    return deep_research_fast.fast_text(b"%PDF-fixture", "book.pdf")


@pytest.mark.parametrize("reader", ["text", "pdftotext", "pypdf", "macos"])
def test_fast_reads_preserve_more_than_150_pages(monkeypatch, reader):
    pages = [f"Page {page}: exact retained source text." for page in range(1, 302)]
    text, _ = read_text(monkeypatch, reader, "\f".join(pages))
    assert text.split("\f") == pages


@pytest.mark.parametrize("reader", ["text", "pdftotext", "pypdf", "macos"])
def test_extracted_text_byte_limit_still_applies(monkeypatch, reader):
    with pytest.raises(ValueError, match="exceeds output limits") as error:
        read_text(monkeypatch, reader, "é" * 2_000_001)
    detail = preparation_diagnostic(error.value)
    assert detail["code"] == "sources.fast_text_limit"
    assert "4 MB" in detail["message"] and "page" not in detail["message"]


@pytest.mark.skipif(sys.platform != "linux", reason="The isolated PDF reader enforces Linux resource limits")
def test_isolated_pdf_reader_extracts_all_301_pages():
    pypdf = pytest.importorskip("pypdf")
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
    writer = pypdf.PdfWriter()
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
    for number in range(1, 302):
        page = writer.add_blank_page(width=612, height=792)
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"):
            DictionaryObject({NameObject("/F1"): font})})
        content = DecodedStreamObject()
        content.set_data(f"BT /F1 12 Tf 20 20 Td (Page {number}: exact retained source text.) Tj ET".encode())
        page[NameObject("/Contents")] = content
    source = BytesIO()
    writer.write(source)
    result = subprocess.run([sys.executable, deep_research_fast_pdf.__file__], input=source.getvalue(),
        capture_output=True, check=True, timeout=30)
    pages = result.stdout.decode().split("\f")
    assert len(pages) == 301
    assert all(page.strip() == f"Page {number}: exact retained source text."
        for number, page in enumerate(pages, start=1))


def test_cli_fast_ingestion_and_replay_preserve_long_paginated_text(tmp_path, capsys):
    from nima_semantica.cli import main
    from nima_semantica.installation import Installation, ModelProfile, write_json
    from nima_semantica.storage import GraphStore
    config = tmp_path / "installation.json"
    data_root = tmp_path / "data"
    write_json(config, Installation(data_root=str(data_root), llm=ModelProfile(model="unused")).model_dump())
    source = tmp_path / "book.txt"
    text = "\f".join(f"Page {number}: exact retained source text." for number in range(1, 302))
    source.write_text(text)
    command = ["--config", str(config), "ingest", "papers", str(source), "--mode", "fast"]
    assert main(command) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["data"]["index_ready"]
    with closing(GraphStore(data_root / "corpus")) as store:
        descriptor, = [record for _, record in store.records("SourceDescriptor")]
        normalized, = [record for _, record in store.records("NormalizedDocument")]
        retained = store.read_artifact(normalized.content["artifact_id"]).decode()
        assert retained.endswith(text) and retained.count("\f") == 300
        assert descriptor.content["metadata"]["preparation_quality"] == "fast_provisional"
        revision = store.revision
    assert main(command) == 0
    replay = json.loads(capsys.readouterr().out)
    assert replay == first
    with closing(GraphStore(data_root / "corpus")) as store:
        assert store.revision == revision
