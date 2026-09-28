"""Dependency metadata alone does not establish LFX extension compatibility."""
import importlib

import pytest


def test_openai_extension_imports_with_pinned_lfx():
    pytest.importorskip("lfx_openai")
    for name in ("openai", "openai_chat_model"):
        importlib.import_module("lfx_openai.components.openai." + name)
