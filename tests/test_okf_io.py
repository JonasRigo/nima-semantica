from pathlib import Path
import pytest

from nima_semantica.okf_io import OKFBundle, OKFConceptDocument, export_bundle, import_bundle


def test_concept_markdown_round_trip_preserves_extensions():
    concept = OKFConceptDocument(
        concept_id="claims/lower_bound",
        type="Claim",
        title="Candidate lower bound",
        tags=("hypothesis", "review"),
        body="# Statement\n\nThe bound may hold.",
        extensions={"project_id": "project-a", "status": "draft"},
    )

    restored = OKFConceptDocument.from_markdown(concept.concept_id, concept.to_markdown())

    assert restored == concept


def test_bundle_export_import_ignores_reserved_files(tmp_path: Path):
    bundle = OKFBundle(
        concepts=(
            OKFConceptDocument(concept_id="notes/question", type="Question", body="Why?"),
            OKFConceptDocument(concept_id="claims/answer", type="Claim", body="Because."),
        )
    )
    tmp_path = tmp_path / "bundle"
    export_bundle(bundle, tmp_path)
    (tmp_path / "index.md").write_text("generated index", encoding="utf-8")

    restored = import_bundle(tmp_path)

    assert [concept.concept_id for concept in restored.concepts] == [
        "claims/answer",
        "notes/question",
    ]
    assert {k: v for k, v in restored.files().items() if k != "index.md"} == bundle.files()
    assert restored.index_body == "generated index"


def test_export_does_not_replace_destination_created_during_publication(tmp_path, monkeypatch):
    import nima_semantica.okf_io as io
    destination = tmp_path / "bundle ü"
    rename = io._rename_directory_noreplace
    def race(parent, source, target):
        destination.mkdir()
        return rename(parent, source, target)
    monkeypatch.setattr(io, "_rename_directory_noreplace", race)
    with pytest.raises(FileExistsError):
        export_bundle(OKFBundle(), destination)
    assert destination.is_dir() and list(destination.iterdir()) == []
    assert list(tmp_path.iterdir()) == [destination]
