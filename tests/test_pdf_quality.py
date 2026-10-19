# Copyright 2026 NIMA-AGI contributors
# SPDX-License-Identifier: Apache-2.0
from nima_semantica.pdf._ported import (
    _latex_quality_issues, _requires_tight_formula_retry,
    _dominant_horizontal_ink_crop,
)

def test_formula_quality_gate_rejects_ambiguous_or_fragmented_latex() -> None:
    assert _latex_quality_issues("$x^2$") == []
    assert "unbalanced_dollar_delimiters" in _latex_quality_issues("$x^2")
    assert "html_contamination" in _latex_quality_issues("&lt;x&gt;")
    assert "fragmented_ocr_tokens" in _latex_quality_issues("a b c d e f")
    assert "repeated_token_block" in _latex_quality_issues(("\\alpha + ") * 20)
    assert "excessive_length" in _latex_quality_issues("x" * 9000)


def test_tight_formula_retry_is_limited_to_crop_sensitive_failures() -> None:
    assert _requires_tight_formula_retry(["fragmented_ocr_tokens"])
    assert _requires_tight_formula_retry(["unbalanced_braces"])
    assert _requires_tight_formula_retry(["fragmented_ocr_tokens", "unbalanced_braces"])
    assert not _requires_tight_formula_retry(["html_contamination"])


def test_dominant_horizontal_ink_crop_excludes_clipped_neighboring_prose() -> None:
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (240, 80), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((20, 20, 220, 38), fill="black")
    draw.rectangle((20, 70, 90, 79), fill="black")

    cropped = _dominant_horizontal_ink_crop(image)

    assert cropped is not None
    assert cropped.width == image.width
    assert cropped.height < image.height


def test_dominant_horizontal_ink_crop_preserves_balanced_multiline_regions() -> None:
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (240, 80), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((20, 10, 220, 25), fill="black")
    draw.rectangle((20, 55, 220, 70), fill="black")

    assert _dominant_horizontal_ink_crop(image) is None
