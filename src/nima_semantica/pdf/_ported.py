# Copyright 2026 NIMA-AGI contributors
# SPDX-License-Identifier: Apache-2.0
# Adapted for NIMA-Semantica; see THIRD_PARTY_NOTICES.md for origin and changes.
"""Standalone port of the source parser, with no NIMA-AGI runtime dependency."""
from __future__ import annotations
import json
import math
import os
import re
import tempfile
import threading
import unicodedata
from collections.abc import Callable
from contextlib import closing
from difflib import SequenceMatcher
from io import BytesIO
from pathlib import Path
from typing import Any
from .support import PdfError as NimaError, canonical_json, sha256_bytes

_DOCLING_THREAD_STATE = threading.local()
FORMULA_RECOGNITION_PROFILE = "nima-semantica-codeformula-latex-v1"

def _export_formula_text(document):
    """Keep mathematical text nested under native Docling picture nodes."""
    return document.export_to_markdown(traverse_pictures=True)

def _unresolved_formula(item, raw_output, issues, crop_hash):
    """Quarantine failed transcription with its exact source evidence, never guessed LaTeX."""
    provenance = [{"page": locator.page_no, "bbox": locator.bbox.model_dump(mode="json"),
                   "character_span": list(locator.charspan)} for locator in item.prov]
    pages = ", ".join(str(locator["page"]) for locator in provenance)
    return {"item_ref": item.self_ref, "original": item.orig,
        "placeholder": f"[UNRESOLVED FORMULA {item.self_ref} on PDF page {pages}; inspect original crop]",
        "recognizer_output": raw_output, "recognizer_output_hash": sha256_bytes(raw_output.encode("utf-8")),
        "recognition_crop_hash": crop_hash, "provenance": provenance,
        "quality_checks": {"profile": "nima-latex-quality-v1", "passed": False, "issues": list(issues)}}

def _latex_quality_issues(value: str) -> list[str]:
    """Return deterministic reasons a detected formula is not safe canonical LaTeX."""
    issues = []
    folded = value.casefold()
    if not value.strip() or "formula-not-decoded" in folded:
        issues.append("undecoded")
    if "<!--" in value or re.search(r"&(?:lt|gt|amp|nbsp);", value):
        issues.append("html_contamination")
    if "\ufffd" in value or "\x00" in value:
        issues.append("invalid_character")
    if value.count("{") != value.count("}"):
        issues.append("unbalanced_braces")
    if value.count("$") % 2:
        issues.append("unbalanced_dollar_delimiters")
    if re.search(r"(?:\b[A-Za-z]\b\s+){4,}\b[A-Za-z]\b", value):
        issues.append("fragmented_ocr_tokens")
    tokens = re.findall(r"\\[A-Za-z]+|[A-Za-z0-9]+|[^\s]", value)
    if len(value) > 8192 or len(tokens) > 2048:
        issues.append("excessive_length")
    if _has_repeated_token_block(tokens[:4096]):
        issues.append("repeated_token_block")
    return sorted(set(issues))


def _has_repeated_token_block(tokens: list[str]) -> bool:
    """Detect runaway decoder loops before crop retries consume more model time."""
    for width in range(1, min(8, len(tokens) // 8) + 1):
        span = width * 8
        for start in range(0, len(tokens) - span + 1):
            block = tokens[start : start + width]
            if all(
                tokens[start + repeat * width : start + (repeat + 1) * width] == block
                for repeat in range(1, 8)
            ):
                return True
    return False


def _relation_context(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = normalized.replace("−", "-").replace("↦→", "↦")
    return "".join(character for character in normalized if not character.isspace())


def _select_negated_equality(text: str, raw_before: str, raw_after: str) -> int | None:
    """Select one equality only when the native-PDF context identifies it safely."""
    candidates = [index for index, character in enumerate(text) if character == "="]
    if len(candidates) == 1:
        return candidates[0]
    expected_before = _relation_context(raw_before)[-60:]
    expected_after = _relation_context(raw_after)[:60]
    scores = []
    for index in candidates:
        actual_before = _relation_context(text[max(0, index - 90) : index])[-60:]
        actual_after = _relation_context(text[index + 1 : index + 91])[:60]
        score = (
            SequenceMatcher(None, expected_before, actual_before).ratio()
            + SequenceMatcher(None, expected_after, actual_after).ratio()
        ) / 2
        scores.append((score, index))
    scores.sort(reverse=True)
    if not scores or scores[0][0] < 0.58:
        return None
    if len(scores) > 1 and scores[0][0] - scores[1][0] < 0.10:
        return None
    return scores[0][1]


def _remove_redundant_or_repair_orphan_negations(
    document: Any, formula_label: Any, source_path: Path, *, unresolved_items=None
) -> list[dict[str, Any]]:
    """Join detached PDF negation strokes using geometry and the native text layer."""
    import pypdfium2 as pdfium

    repairs: list[dict[str, Any]] = []
    formulas = [item for item in document.texts if item.label == formula_label]
    for item in document.texts:
        if item.label == formula_label or item.text.strip() not in {"̸", "≠"}:
            continue
        if not item.prov:
            raise NimaError(
                                "Detached mathematical negation glyph has no source coordinates",
                details={"item": item.self_ref},
            )
        glyph = item.prov[0]
        overlapping = []
        for formula in formulas:
            for locator in formula.prov:
                if locator.page_no != glyph.page_no:
                    continue
                x = glyph.bbox.l
                y = (glyph.bbox.t + glyph.bbox.b) / 2
                if locator.bbox.l <= x <= locator.bbox.r and locator.bbox.b <= y <= locator.bbox.t:
                    overlapping.append(formula)
                    break
        if len(overlapping) == 1 and re.search(r"\\(?:neq|ne|not)|≠", overlapping[0].text):
            item.text = ""
            repairs.append(
                {
                    "kind": "removed_redundant_negation_overlay",
                    "item_ref": item.self_ref,
                    "formula_item_ref": overlapping[0].self_ref,
                    "page": glyph.page_no,
                    "bbox": glyph.bbox.model_dump(mode="json"),
                }
            )
            continue
        glyph_x = glyph.bbox.l
        glyph_y = (glyph.bbox.t + glyph.bbox.b) / 2
        overlapping_text = []
        for candidate in document.texts:
            if candidate is item or candidate.label == formula_label:
                continue
            for locator in candidate.prov:
                if locator.page_no != glyph.page_no:
                    continue
                if (
                    locator.bbox.l - 2 <= glyph_x <= locator.bbox.r + 10
                    and locator.bbox.b - 3 <= glyph_y <= locator.bbox.t + 3
                ):
                    overlapping_text.append(candidate)
                    break
        native_candidates: list[tuple[float, int]] = []
        with closing(pdfium.PdfDocument(str(source_path))) as pdf:
            with closing(pdf[glyph.page_no - 1]) as native_pdf_page:
                with closing(native_pdf_page.get_textpage()) as native_page:
                    native_text = native_page.get_text_range()
                    for index, character in enumerate(native_text):
                        if character != "̸":
                            continue
                        box = native_page.get_charbox(index)
                        native_x = (box[0] + box[2]) / 2
                        native_y = (box[1] + box[3]) / 2
                        native_candidates.append(
                            (abs(native_x - glyph_x) + abs(native_y - glyph_y), index)
                        )
        native_candidates.sort()
        if len(overlapping_text) == 1 and native_candidates and native_candidates[0][0] <= 12:
            distance, native_index = native_candidates[0]
            relation_tail = native_text[native_index + 1 : native_index + 8]
            equality_match = re.match(r"\s*=", relation_tail)
            if equality_match:
                target = overlapping_text[0]
                target_index = _select_negated_equality(
                    target.text,
                    native_text[max(0, native_index - 90) : native_index],
                    native_text[native_index + 1 + equality_match.end() : native_index + 92],
                )
                if target_index is not None:
                    original_text = target.text
                    target.text = (
                        f"{original_text[:target_index]}≠"
                        f"{original_text[target_index + 1:]}"
                    )
                    item.text = ""
                    repairs.append(
                        {
                            "kind": "joined_native_text_negation_overlay",
                            "item_ref": item.self_ref,
                            "text_item_ref": target.self_ref,
                            "page": glyph.page_no,
                            "bbox": glyph.bbox.model_dump(mode="json"),
                            "native_text_hash": sha256_bytes(native_text.encode("utf-8")),
                            "native_character_index": native_index,
                            "geometry_distance": distance,
                            "original_text_hash": sha256_bytes(original_text.encode("utf-8")),
                            "repaired_text_hash": sha256_bytes(target.text.encode("utf-8")),
                        }
                    )
                    continue
        details = {
                "item": item.self_ref,
                "page": glyph.page_no,
                "bbox": glyph.bbox.model_dump(mode="json"),
                "overlapping_formula_items": [value.self_ref for value in overlapping],
                "overlapping_text_items": [value.self_ref for value in overlapping_text],
                "quality_issue": "orphan_relational_operator",
            }
        if unresolved_items is not None:
            # Quarantine every possible operand, not just the detached stroke:
            # leaving an unnegated equality searchable would change the claim.
            affected = overlapping + overlapping_text
            if not affected:
                affected = [value for value in document.texts
                            if any(p.page_no == glyph.page_no for p in value.prov)]
            for value in [item, *affected]:
                if not any(existing.self_ref == value.self_ref for existing, _ in unresolved_items):
                    unresolved_items.append((value, details))
            continue
        raise NimaError("Detached mathematical negation glyph cannot be joined unambiguously", details=details)
    return repairs


_FRAGMENTED_FOR_ALL_TAIL = re.compile(
    r"^(?P<equation>.+?)\s*\\quad\s+f\s+o\s+r\s+\\\s*a\s+l\s+l(?:\s|$)",
    flags=re.IGNORECASE,
)
_FRAGMENTED_INTERTEXT_TAIL = re.compile(
    r"^(?P<equation>.+?)(?:\s*\\\\)+\s*\\intertext\s*\{\s*(?:[A-Za-z]\s+){4,}",
    flags=re.IGNORECASE,
)


def _exclude_fragmented_for_all_tail(value: str) -> tuple[str, dict[str, str] | None]:
    """Remove only an OCR-corrupted prose quantifier from an otherwise valid formula.

    Docling may classify a displayed equation and its adjacent ``for all ...`` prose as one
    formula crop. CodeFormulaV2 then sometimes returns the equation as LaTeX followed by
    character-spaced prose. The surrounding Docling text item retains that prose, so it must
    not be accepted as mathematical notation. Every other quality failure remains fail-closed.
    """
    normalized = value.strip()
    match = _FRAGMENTED_FOR_ALL_TAIL.match(normalized)
    kind = "excluded_fragmented_for_all_prose_tail"
    if match is None:
        match = _FRAGMENTED_INTERTEXT_TAIL.match(normalized)
        kind = "excluded_fragmented_intertext_prose_tail"
    if match is None:
        return normalized, None
    equation = match.group("equation").strip()
    if not equation:
        return normalized, None
    return equation, {
        "kind": kind,
        "raw_latex_hash": sha256_bytes(normalized.encode("utf-8")),
    }


def _requires_tight_formula_retry(quality_issues: list[str]) -> bool:
    """Retry crop-sensitive failures once against the exact layout region."""
    return bool({"fragmented_ocr_tokens", "unbalanced_braces"} & set(quality_issues))


def _dominant_horizontal_ink_crop(image: Any) -> Any | None:
    """Isolate a formula line when its layout box clips adjacent prose.

    This conservative last-resort crop activates only when whitespace separates multiple ink
    bands and one band contains a clear majority of the dark pixels. Balanced multi-line
    formula regions therefore remain untouched.
    """
    grayscale = image.convert("L")
    width, height = grayscale.size
    if width < 8 or height < 8:
        return None
    pixels = grayscale.load()
    minimum_ink = max(2, math.ceil(width * 0.0025))
    row_ink = [sum(1 for x in range(width) if pixels[x, y] < 200) for y in range(height)]
    active_rows = [index for index, ink in enumerate(row_ink) if ink >= minimum_ink]
    if not active_rows:
        return None
    bands: list[tuple[int, int]] = []
    maximum_internal_gap = max(3, round(height * 0.12))
    start = previous = active_rows[0]
    for row in active_rows[1:]:
        if row - previous > maximum_internal_gap:
            bands.append((start, previous + 1))
            start = row
        previous = row
    bands.append((start, previous + 1))
    if len(bands) < 2:
        return None
    band_ink = [sum(row_ink[top:bottom]) for top, bottom in bands]
    interior_indices = [
        index for index, (top, bottom) in enumerate(bands) if top > 0 and bottom < height
    ]
    dominant_index = max(range(len(bands)), key=band_ink.__getitem__)
    total_ink = sum(band_ink)
    edge_fragment_case = (
        len(interior_indices) == 1
        and len(bands) > 1
        and (bands[interior_indices[0]][1] - bands[interior_indices[0]][0])
        >= 1.25
        * max(
            bottom - top
            for index, (top, bottom) in enumerate(bands)
            if index != interior_indices[0]
        )
    )
    if edge_fragment_case:
        dominant_index = interior_indices[0]
    elif not total_ink or band_ink[dominant_index] / total_ink < 0.55:
        return None
    top, bottom = bands[dominant_index]
    padding = max(2, round(height * 0.04))
    top = max(0, top - padding)
    bottom = min(height, bottom + padding)
    if top == 0 and bottom == height:
        return None
    return image.crop((0, top, width, bottom))


def _thread_cached_component(name: str, key: Any, factory: Callable[[], Any]) -> Any:
    """Reuse expensive parser components within, but never across, worker threads."""
    cached_key = getattr(_DOCLING_THREAD_STATE, f"{name}_key", None)
    cached = getattr(_DOCLING_THREAD_STATE, name, None)
    if cached is None or cached_key != key:
        cached = factory()
        setattr(_DOCLING_THREAD_STATE, name, cached)
        setattr(_DOCLING_THREAD_STATE, f"{name}_key", key)
    return cached
def _parse_docling_with_latex(
    file_path: str | Path, file_type: str | None = None, **options: Any
) -> dict[str, Any]:
    """Parse once, then recognize only unresolved formula regions with a pinned model."""
    del file_type
    from docling.datamodel.base_models import InputFormat, ItemAndImageEnrichmentElement
    from docling.datamodel.pipeline_options import CodeFormulaVlmOptions, PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling.models.stages.code_formula.code_formula_vlm_model import CodeFormulaVlmModel
    from docling_core.types.doc import DocItemLabel

    formula_model_path = Path(str(options.pop("formula_model_path"))).resolve(strict=True)
    formula_model_manifest_hash = str(options.pop("formula_model_manifest_hash"))
    artifact_dir = Path(str(options.pop("formula_artifact_dir"))).resolve()
    artifact_prefix = str(options.pop("formula_artifact_prefix"))
    progress = options.pop("progress", None) or (lambda _event: None)
    allow_partial = options.pop("allow_partial", False)
    expected_formulas = options.pop("expected_formulas", [])
    expected_by_crop = {
        str(item.get("crop_hash")): item
        for item in expected_formulas
        if isinstance(item, dict) and item.get("crop_hash")
    }
    prefix_path = Path(artifact_prefix)
    if prefix_path.is_absolute() or ".." in prefix_path.parts:
        raise NimaError("Invalid formula artifact prefix")
    artifact_dir.mkdir(parents=True, exist_ok=True)
    code_formula = CodeFormulaVlmOptions.from_preset("codeformulav2")
    local_spec = code_formula.model_spec.model_copy(
        update={"revision": "ecedbe111d15c2dc60bfd4a823cbe80127b58af4"}
    )
    pipeline = PdfPipelineOptions(
        artifacts_path=formula_model_path,
        enable_remote_services=False,
        allow_external_plugins=False,
        document_timeout=600,
        do_formula_enrichment=False,
        do_code_enrichment=False,
        generate_page_images=True,
        images_scale=CodeFormulaVlmModel.images_scale,
    )
    converter = _thread_cached_component(
        "converter",
        (str(formula_model_path), formula_model_manifest_hash),
        lambda: DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline)}
        ),
    )
    result = converter.convert(str(file_path))
    if result.status.value != "success":
        raise NimaError("PDF conversion did not complete successfully")
    document = result.document
    detected = [item for item in document.texts if item.label == DocItemLabel.FORMULA]
    progress(
        {
            "phase": "layout",
            "detail": f"detected {len(detected)} formula regions",
            "formula_regions": len(detected),
        }
    )
    pending: list[tuple[Any, Any, bytes, str, Path, Path]] = []
    recognition: dict[str, tuple[str, str]] = {}
    recognition_repairs: dict[str, dict[str, Any]] = {}
    regions: dict[str, tuple[bytes, str]] = {}
    checkpoint_records: list[dict[str, Any]] = []
    unresolved_formulas: list[dict[str, Any]] = []

    def checkpoint_details() -> dict[str, Any]:
        checkpoint = {
            "profile": FORMULA_RECOGNITION_PROFILE,
            "formula_model_manifest_hash": formula_model_manifest_hash,
            "formulas": checkpoint_records,
        }
        return {
            **checkpoint,
            "manifest_hash": sha256_bytes(canonical_json(checkpoint)),
        }

    for item in detected:
        if not item.prov:
            raise NimaError(
                                "Docling detected a formula without a source region",
                details={"item": item.self_ref},
            )
        locator = item.prov[0]
        page = document.pages.get(locator.page_no)
        page_image = page.image.pil_image if page is not None and page.image is not None else None
        if page is None or page_image is None:
            raise NimaError(
                                "Docling did not retain the page image required for formula recognition",
                details={"item": item.self_ref, "page": locator.page_no},
            )
        bbox = locator.bbox.expand_by_scale(
            CodeFormulaVlmModel.expansion_factor,
            CodeFormulaVlmModel.expansion_factor,
        )
        bbox = bbox.to_top_left_origin(page.size.height).scaled(CodeFormulaVlmModel.images_scale)
        crop = page_image.crop((bbox.l, bbox.t, bbox.r, bbox.b))
        buffer = BytesIO()
        crop.save(buffer, format="PNG")
        crop_bytes = buffer.getvalue()
        crop_hash = sha256_bytes(crop_bytes)
        crop_path = artifact_dir / f"{crop_hash}.png"
        regions[item.self_ref] = (crop_bytes, crop_hash)
        _atomic_formula_artifact(crop_path, crop_bytes)
        # Formula enrichment is disabled during layout. Its native PDF text
        # (e.g. stacked fractions flattened to "1 2") is not trusted LaTeX,
        # even when it happens to pass a syntactic quality check.
        cache_key = sha256_bytes(
            canonical_json(
                {
                    "crop_hash": crop_hash,
                    "formula_model_manifest_hash": formula_model_manifest_hash,
                    "recognition_profile": FORMULA_RECOGNITION_PROFILE,
                }
            )
        )
        cache_path = artifact_dir / f"{cache_key}.json"
        expected_formula = expected_by_crop.get(crop_hash)
        if cache_path.exists() and expected_formula is not None:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            expected = {
                "crop_hash": crop_hash,
                "formula_model_manifest_hash": formula_model_manifest_hash,
                "recognition_key": cache_key,
                "recognition_profile": FORMULA_RECOGNITION_PROFILE,
            }
            if any(cached.get(key) != value for key, value in expected.items()):
                raise NimaError("Formula recognition cache mismatch")
            latex = str(cached.get("latex", "")).strip()
            if (
                not latex
                or "formula-not-decoded" in latex.casefold()
                or cached.get("latex_hash") != sha256_bytes(latex.encode("utf-8"))
                or cached.get("latex_hash") != expected_formula.get("latex_hash")
                or latex != expected_formula.get("latex")
            ):
                raise NimaError("Formula recognition cache is corrupt")
            item.text = latex
            recognition[item.self_ref] = (latex, "pinned-recognizer")
            checkpoint_records.append(dict(cached))
            continue
        pending.append((item, crop, crop_bytes, crop_hash, crop_path, cache_path))

    progress(
        {
            "phase": "formula_recognition",
            "detail": f"recognizing {len(pending)} unresolved formula regions",
            "formula_regions": len(detected),
            "pending_regions": len(pending),
            "reused_regions": len(detected) - len(pending),
        }
    )

    if pending:
        recognizer_options = code_formula.model_copy(
            update={
                "model_spec": local_spec,
                "extract_code": False,
                "extract_formulas": True,
            }
        )
        recognizer_key = (str(formula_model_path), formula_model_manifest_hash)
        recognizer = _thread_cached_component(
            "formula_recognizer",
            recognizer_key,
            lambda: CodeFormulaVlmModel(
                enabled=True,
                enable_remote_services=False,
                artifacts_path=formula_model_path,
                options=recognizer_options,
                accelerator_options=pipeline.accelerator_options,
            ),
        )
        for offset in range(0, len(pending), recognizer.elements_batch_size):
            batch = pending[offset : offset + recognizer.elements_batch_size]
            elements = [
                ItemAndImageEnrichmentElement(item=item, image=crop) for item, crop, *_ in batch
            ]
            outputs = list(recognizer(document, elements))
            if len(outputs) != len(batch):
                raise NimaError("Formula recognizer lost a region")
            for output, (item, _, _, crop_hash, _, cache_path) in zip(outputs, batch, strict=True):
                raw_latex = output.text.strip()
                crop_hash_for_failure = crop_hash
                latex, repair = _exclude_fragmented_for_all_tail(raw_latex)
                quality_issues = _latex_quality_issues(latex)
                if _requires_tight_formula_retry(quality_issues):
                    locator = item.prov[0]
                    page = document.pages[locator.page_no]
                    page_image = page.image.pil_image
                    tight_bbox = locator.bbox.to_top_left_origin(page.size.height).scaled(
                        CodeFormulaVlmModel.images_scale
                    )
                    tight_crop = page_image.crop(
                        (tight_bbox.l, tight_bbox.t, tight_bbox.r, tight_bbox.b)
                    )
                    tight_buffer = BytesIO()
                    tight_crop.save(tight_buffer, format="PNG")
                    tight_crop_bytes = tight_buffer.getvalue()
                    tight_crop_hash = sha256_bytes(tight_crop_bytes)
                    _atomic_formula_artifact(
                        artifact_dir / f"{tight_crop_hash}.png", tight_crop_bytes
                    )
                    tight_outputs = list(
                        recognizer(
                            document,
                            [ItemAndImageEnrichmentElement(item=item, image=tight_crop)],
                        )
                    )
                    if len(tight_outputs) != 1:
                        raise NimaError(
                                                        "Formula recognizer lost a tight-crop retry region",
                        )
                    tight_raw_latex = tight_outputs[0].text.strip()
                    tight_latex, initial_tight_repair = _exclude_fragmented_for_all_tail(
                        tight_raw_latex
                    )
                    tight_repair: dict[str, Any] | None = initial_tight_repair
                    tight_quality_issues = _latex_quality_issues(tight_latex)
                    band_crop_hash: str | None = None
                    band_raw_latex: str | None = None
                    band_crop = tight_crop
                    band_crop_hashes: list[str] = []
                    for _band_retry in range(2):
                        if not _requires_tight_formula_retry(tight_quality_issues):
                            break
                        band_crop = _dominant_horizontal_ink_crop(band_crop)
                        if band_crop is not None:
                            band_buffer = BytesIO()
                            band_crop.save(band_buffer, format="PNG")
                            band_crop_bytes = band_buffer.getvalue()
                            band_crop_hash = sha256_bytes(band_crop_bytes)
                            band_crop_hashes.append(band_crop_hash)
                            _atomic_formula_artifact(
                                artifact_dir / f"{band_crop_hash}.png", band_crop_bytes
                            )
                            band_outputs = list(
                                recognizer(
                                    document,
                                    [ItemAndImageEnrichmentElement(item=item, image=band_crop)],
                                )
                            )
                            if len(band_outputs) != 1:
                                raise NimaError(
                                                                        "Formula recognizer lost a dominant-band retry region",
                                )
                            band_raw_latex = band_outputs[0].text.strip()
                            band_latex, band_repair = _exclude_fragmented_for_all_tail(
                                band_raw_latex
                            )
                            band_quality_issues = _latex_quality_issues(band_latex)
                            tight_raw_latex = band_raw_latex
                            tight_latex = band_latex
                            tight_quality_issues = band_quality_issues
                            if not band_quality_issues:
                                tight_repair = {
                                    "kind": "retried_dominant_horizontal_ink_band",
                                    "tight_crop_hash": tight_crop_hash,
                                    "recognition_crop_hash": band_crop_hash,
                                    "crop_chain_hashes": band_crop_hashes,
                                }
                                if band_repair is not None:
                                    tight_repair["band_crop_repair_kind"] = band_repair["kind"]
                        else:
                            break
                    if not tight_quality_issues:
                        latex = tight_latex
                        quality_issues = []
                        recognition_crop_hash = tight_crop_hash
                        repair_kind = "retried_unexpanded_formula_crop"
                        if (
                            tight_repair is not None
                            and tight_repair["kind"] == "retried_dominant_horizontal_ink_band"
                        ):
                            recognition_crop_hash = tight_repair["recognition_crop_hash"]
                            repair_kind = tight_repair["kind"]
                        repair = {
                            "kind": repair_kind,
                            "raw_latex_hash": sha256_bytes(raw_latex.encode("utf-8")),
                            "expanded_crop_hash": crop_hash,
                            "tight_crop_hash": tight_crop_hash,
                            "recognition_crop_hash": recognition_crop_hash,
                        }
                        if tight_repair is not None:
                            repair["tight_crop_repair_kind"] = tight_repair["kind"]
                    else:
                        raw_latex = band_raw_latex or tight_raw_latex
                        quality_issues = tight_quality_issues
                        crop_hash_for_failure = band_crop_hash or tight_crop_hash
                if quality_issues:
                    if allow_partial:
                        failure = _unresolved_formula(item, raw_latex, quality_issues, crop_hash_for_failure)
                        _atomic_formula_artifact(artifact_dir / (sha256_bytes(canonical_json(failure)) + ".json"),
                            canonical_json(failure))
                        unresolved_formulas.append(failure)
                        item.text = failure["placeholder"]
                        output.text = item.text
                        recognition[item.self_ref] = (item.text, "unresolved")
                        continue
                    raise NimaError(
                                                "Formula recognizer returned invalid LaTeX",
                        details={
                            "item": output.self_ref,
                            "quality_issues": quality_issues,
                            "recognizer_output_hash": sha256_bytes(raw_latex.encode("utf-8")),
                            "recognizer_output": raw_latex,
                            "recognition_crop_hash": crop_hash_for_failure,
                            "formula_checkpoint": checkpoint_details(),
                        },
                    )
                record = {
                    "crop_hash": crop_hash,
                    "formula_model_manifest_hash": formula_model_manifest_hash,
                    "recognition_key": cache_path.stem,
                    "recognition_profile": FORMULA_RECOGNITION_PROFILE,
                    "latex": latex,
                    "raw_recognizer_output": raw_latex,
                    "latex_hash": sha256_bytes(latex.encode("utf-8")),
                }
                if repair is not None:
                    record["recognition_repair"] = repair
                _atomic_formula_artifact(cache_path, canonical_json(record))
                checkpoint_records.append(record)
                item.text = latex
                output.text = latex
                recognition[item.self_ref] = (latex, "pinned-recognizer")
                if repair is not None:
                    recognition_repairs[item.self_ref] = repair
            progress(
                {
                    "phase": "formula_recognition",
                    "detail": (
                        f"processed {min(offset + len(batch), len(pending))}/{len(pending)} "
                        "formula regions"
                    ),
                    "processed_regions": min(offset + len(batch), len(pending)),
                    "recognized_regions": min(offset + len(batch), len(pending)) - len(unresolved_formulas),
                    "pending_regions": len(pending),
                    "unresolved_regions": len(unresolved_formulas),
                }
            )

    ambiguous_negations = []
    negation_repairs = _remove_redundant_or_repair_orphan_negations(
        document, DocItemLabel.FORMULA, Path(file_path),
        unresolved_items=ambiguous_negations if allow_partial else None)
    for item, details in ambiguous_negations:
        if any(failure["item_ref"] == item.self_ref for failure in unresolved_formulas):
            continue
        locator = item.prov[0]
        page = document.pages[locator.page_no]
        bbox = locator.bbox.to_top_left_origin(page.size.height).scaled(CodeFormulaVlmModel.images_scale)
        # Detached strokes can have zero-width PDF boxes; retain visible context.
        crop = page.image.pil_image.crop((bbox.l - 4, bbox.t - 4, bbox.r + 4, bbox.b + 4))
        buffer = BytesIO()
        crop.save(buffer, format="PNG")
        crop_bytes = buffer.getvalue()
        crop_hash = sha256_bytes(crop_bytes)
        _atomic_formula_artifact(artifact_dir / f"{crop_hash}.png", crop_bytes)
        failure = _unresolved_formula(item, item.text, ["orphan_relational_operator"], crop_hash)
        failure["ambiguity"] = details
        unresolved_formulas.append(failure)
        _atomic_formula_artifact(artifact_dir / f"{sha256_bytes(canonical_json(failure))}.json", canonical_json(failure))
        item.text = failure["placeholder"]
        recognition[item.self_ref] = (item.text, "unresolved")
    formulas = []
    for item in detected:
        if recognition[item.self_ref][1] == "unresolved":
            continue
        latex = item.text.strip()
        quality_issues = _latex_quality_issues(latex)
        if quality_issues:
            raise NimaError(
                                "Docling left a detected mathematical expression invalid",
                details={
                    "item": item.self_ref,
                    "quality_issues": quality_issues,
                    "formula_checkpoint": checkpoint_details(),
                },
            )
        provenance = []
        for locator in item.prov:
            provenance.append(
                {
                    "page": locator.page_no,
                    "bbox": locator.bbox.model_dump(mode="json"),
                    "character_span": list(locator.charspan),
                }
            )
        formulas.append(
            {
                "item_ref": item.self_ref,
                "latex": latex,
                "latex_hash": sha256_bytes(latex.encode("utf-8")),
                "raw": item.orig,
                "recognition_method": recognition[item.self_ref][1],
                "quality_checks": {
                    "profile": "nima-latex-quality-v1",
                    "passed": True,
                    "issues": [],
                },
                "provenance": provenance,
            }
        )
        if item.self_ref in recognition_repairs:
            formulas[-1]["recognition_repair"] = recognition_repairs[item.self_ref]
        crop_bytes, crop_hash = regions[item.self_ref]
        formulas[-1].update(
            {
                "crop_hash": crop_hash,
                "crop_artifact_path": f"{artifact_prefix}/{crop_hash}.png",
                "crop_bytes": len(crop_bytes),
            }
        )
    text = _export_formula_text(document)
    if "formula-not-decoded" in text.casefold():
        raise NimaError(
                        "Mathematical normalization is incomplete; undecoded formula marker remains",
        )
    cursor = 0
    for formula in formulas:
        start = text.find(formula["latex"], cursor)
        if start < 0:
            raise NimaError(
                                "Normalized LaTeX formula is absent from the exported document",
                details={
                    "item": formula["item_ref"],
                    "formula_checkpoint": checkpoint_details(),
                },
            )
        formula["normalized_text_start"] = start
        formula["normalized_text_end"] = start + len(formula["latex"])
        cursor = formula["normalized_text_end"]
    for failure in unresolved_formulas:
        start = text.find(failure["placeholder"])
        if start < 0:
            raise NimaError("Unresolved formula placeholder absent from exported document")
        failure["normalized_text_start"] = start
        failure["normalized_text_end"] = start + len(failure["placeholder"])
    return {
        "full_text": text,
        "source_items": [
            {"item_ref": item.self_ref, "text": item.text, "original": item.orig,
             "kind": str(item.label.value), "provenance": [
                 {"page": locator.page_no, "bbox": locator.bbox.model_dump(mode="json"),
                  "character_span": list(locator.charspan)} for locator in item.prov]}
            for item in document.texts if item.text.strip()
        ],
        "total_pages": len(result.pages),
        "export_format": "markdown+latex",
        "mathematics": {
            "format": "latex",
            "delimiter": "dollar",
            "formula_count": len(formulas),
            "unresolved_formula_count": len(unresolved_formulas),
            "unresolved_formulas": unresolved_formulas,
            "recognizer_target_count": sum(
                method == "pinned-recognizer" for _, method in recognition.values()
            ),
            "recognition_scope": "detected-unresolved-regions",
            "recognition_profile": FORMULA_RECOGNITION_PROFILE,
            "negation_repairs": negation_repairs,
            "formulas": formulas,
        },
    }


def _atomic_formula_artifact(path: Path, content: bytes) -> None:
    """Write a derived non-pickle formula artifact without exposing a partial file."""
    if path.exists():
        if sha256_bytes(path.read_bytes()) != sha256_bytes(content):
            raise NimaError("Formula artifact hash collision")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
