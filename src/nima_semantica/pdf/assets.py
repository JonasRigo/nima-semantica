"""Verify locally owned model assets before any PDF inference."""
import json
from importlib import metadata, resources
from pathlib import Path
from packaging.specifiers import SpecifierSet
from .support import PdfError, canonical_json, sha256_bytes, sha256_file


def model_manifest():
    return json.loads(resources.files(__package__).joinpath("model_manifest.json").read_text())


def verify_assets(root, manifest=None, *, check_versions=True):
    root = Path(root).resolve(strict=True)
    manifest = manifest or model_manifest()
    for artifact in manifest["approved_artifacts"]:
        candidate = (root / artifact["path"]).resolve(strict=True)
        if not candidate.is_relative_to(root):
            raise PdfError("model asset escapes owned directory")
        if candidate.stat().st_size != artifact["size"] or sha256_file(candidate) != artifact["sha256"]:
            raise PdfError("model asset integrity mismatch", details={"path": artifact["path"]})
    versions = {}
    if check_versions:
        for name in ("docling", "transformers", "torch"):
            versions[name] = metadata.version(name)
            if versions[name] not in SpecifierSet(manifest["compatible_" + name]):
                raise PdfError("incompatible PDF runtime", details={"package": name, "version": versions[name]})
    code_hashes = {name: sha256_file(Path(__file__).with_name(name)) for name in ("_ported.py", "worker.py", "support.py")}
    result = {"manifest_hash": sha256_bytes(canonical_json(manifest)), "versions": versions, "code_hashes": code_hashes}
    result["profile_hash"] = sha256_bytes(canonical_json(result))
    return result


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("prepare", "verify"))
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    if args.action == "prepare":
        from docling.utils.model_downloader import download_models
        args.root.mkdir(parents=True, exist_ok=True)
        download_models(output_dir=args.root.resolve(), with_layout=True, with_tableformer=True,
            with_tableformer_v2=False, with_code_formula=True, with_picture_classifier=False,
            with_rapidocr=True, rapidocr_models=["onnxruntime:chinese"])
    print(json.dumps(verify_assets(args.root)))


if __name__ == "__main__":
    main()
