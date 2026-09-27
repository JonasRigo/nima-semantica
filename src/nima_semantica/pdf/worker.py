"""Offline PDF normalization; caches are derived artifacts, never claim authority."""
import base64
import json
import os
from pathlib import Path

from .assets import verify_assets
from .support import PdfError, canonical_json, sha256_bytes, sha256_file


def normalize_pdf(source, models, output, *, progress=None):
    # These must be in force before importing the model framework.
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["ORT_DISABLE_TELEMETRY"] = "1"
    os.environ["OMP_NUM_THREADS"] = "4"
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    identity = verify_assets(models)
    from ._ported import _parse_docling_with_latex, _atomic_formula_artifact
    source, output = Path(source).resolve(strict=True), Path(output).resolve()
    source_hash = sha256_file(source)
    output.mkdir(parents=True, exist_ok=True)
    document_dir = output / source_hash / identity["profile_hash"]
    document_dir.mkdir(parents=True, exist_ok=True)
    result = _parse_docling_with_latex(source, formula_model_path=str(Path(models).resolve()),
        formula_model_manifest_hash=identity["manifest_hash"], formula_artifact_dir=str(document_dir / "artifacts"),
        formula_artifact_prefix="artifacts", progress=progress, allow_partial=True)
    text = result["full_text"]
    diagnostics = []
    for item in result["source_items"]:
        if not item["provenance"]:
            raise PdfError("source text is missing page provenance", details={"item": item["item_ref"]})
        # Original Docling item offsets are not normalized-document offsets.
        diagnostics.append({**item, "kind": "source_item", "source_kind": item["kind"], "status": "extracted", "offset_scope": "original_item"})
    for formula in result["mathematics"]["formulas"]:
        diagnostics.append({**formula, "kind": "formula", "status": "low_confidence",
            "start": formula["normalized_text_start"], "end": formula["normalized_text_end"],
            "note": "Passed syntactic/geometry checks; model transcription is not mathematical verification."})
    for formula in result["mathematics"].get("unresolved_formulas", []):
        diagnostics.append({**formula, "kind": "formula", "status": "unresolved",
            "start": formula["normalized_text_start"], "end": formula["normalized_text_end"],
            "note": "Recognition failed. This placeholder is not a mathematical statement; inspect the original source crop."})
    artifacts = []
    for artifact in sorted((document_dir / "artifacts").iterdir()):
        if artifact.is_file():
            data = artifact.read_bytes()
            artifacts.append({"sha256": sha256_bytes(data), "name": artifact.name, "data_base64": base64.b64encode(data).decode()})
    diagnostics.append({"kind": "parser_manifest", "status": "extracted",
        "source_sha256": source_hash, "normalized_sha256": sha256_bytes(text.encode()),
        "provenance": [{"page": page} for page in range(1, result["total_pages"] + 1)],
        "runtime": identity, "mathematics": result["mathematics"], "artifact_bundle": artifacts})
    response = {"text": text, "diagnostics": diagnostics,
        "normalization_complete": result["mathematics"]["unresolved_formula_count"] == 0}
    _atomic_formula_artifact(document_dir / "result.json", canonical_json(response))
    _atomic_formula_artifact(document_dir / "result.sha256", sha256_bytes(canonical_json(response)).encode())
    return response


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--models", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = normalize_pdf(args.source, args.models, args.output, progress=lambda event: print(json.dumps(event), flush=True))
        print(json.dumps({"status": "success" if result["normalization_complete"] else "partial", "characters": len(result["text"]),
            "formula_count": sum(d["kind"] == "formula" for d in result["diagnostics"])}), flush=True)
    except Exception as exc:
        args.output.mkdir(parents=True, exist_ok=True)
        # Unique failure artifacts retain partial crop/checkpoint evidence.
        from uuid import uuid4
        (args.output / ("failure-" + uuid4().hex + ".json")).write_bytes(canonical_json({"error": str(exc), "type": type(exc).__name__, "details": getattr(exc, "details", {})}))
        raise


if __name__ == "__main__":
    main()
