"""Offline stock-component wiring and saved canvas safety checks."""
import asyncio
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

pytest.importorskip("lfx")
from lfx.schema import DataFrame

from nima_semantica.deep_research_contracts import DeepResearchRequest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "deploy"))
from build_deep_research_simple import build
from build_tool_guide import load_component
from flow_io import validate_edges


def test_simple_canvas_snapshot_and_operator_boundary():
    saved = json.loads((ROOT / "examples/langflow_replacement/deep_research_simple.json").read_text())
    assert saved == build()
    validate_edges(saved)
    assert len(saved["data"]["nodes"]) == 7 and len(saved["data"]["edges"]) == 8
    assert {node["data"]["type"] for node in saved["data"]["nodes"]} >= {
        "DeepResearchPassFields", "ConductPassDeepResearch", "StockArxivHTML", "PaperDiscovery", "ReviewGraphCommit"}
    assert saved["nima_tool_manifest"]["controller_version"] == "deep-research-passes-v11"
    for node in saved["data"]["nodes"]:
        template = node["data"]["node"]["template"]
        for name in ("allow_model_calls", "allow_audit_writes", "allow_fast_read", "allow_source_ingestion", "allow_graph_writes", "approval_json", "acquisition_policy_json"):
            if name in template:
                assert not template[name].get("input_types") and not template[name].get("tool_mode")


def test_one_canvas_preview_and_focus_question_fields():
    from nima_semantica.deep_research_passes import PassResearchRequest
    fields = load_component("DeepResearchPassFields")().set(mode="preview",
        questions_json="[]", graph_revision_json="null", search_phrases_json="[]",
        input_graph_artifact_id="", focus_question="Which assumption remains untested?")
    packet = asyncio.run(fields.request_data()).data
    parsed = PassResearchRequest.model_validate(packet)
    assert parsed.focus_question == "Which assumption remains untested?"
    result = asyncio.run(load_component("ConductPassDeepResearch")().set(payload=packet,
        ).result_data()).data
    assert result["status"] == "complete" and result["data"]["executed"] is False


def test_dynamic_stock_arxiv_tool_uses_short_query_and_406_fallback(monkeypatch):
    from lfx_arxiv.components.arxiv.arxiv import ArXivComponent
    monkeypatch.setattr(ArXivComponent, "search_papers_dataframe", lambda self: DataFrame([{"error": "HTTP 406"}]))
    seen = []
    def fallback(query, *, max_results):
        seen.append((query, max_results))
        return [{"id": "https://arxiv.org/abs/2609.12345", "title": "A Pauli paper",
            "summary": "An abstract", "pdf_url": "https://arxiv.org/pdf/2609.12345"}]
    monkeypatch.setattr("nima_semantica.arxiv_search_fallback.search_page", fallback)
    policy = SimpleNamespace(metadata={"nima_paper_discovery": {"discovery_providers": ["arxiv"], "max_discoveries": 3}})
    component = load_component("StockArxivHTML")().set(paper_discovery=policy, max_results=2, max_html=0)
    tool = asyncio.run(component.build_tool())
    packet = tool.invoke({"query": "Pauli propagation"})
    assert packet["backend"] == "arxiv_search_html_fallback" and packet["outcome"] == "metadata_ready"
    assert seen == [("Pauli propagation", 2)]


def test_stock_arxiv_reports_fallback_no_results_without_fabricating_hits(monkeypatch):
    from lfx_arxiv.components.arxiv.arxiv import ArXivComponent
    monkeypatch.setattr(ArXivComponent, "search_papers_dataframe", lambda self: DataFrame([{"error": "HTTP 406"}]))
    def unavailable(query, *, max_results):
        raise RuntimeError("arXiv search page had no parseable paper identities")
    monkeypatch.setattr("nima_semantica.arxiv_search_fallback.search_page", unavailable)
    policy = SimpleNamespace(metadata={"nima_paper_discovery": {"discovery_providers": ["arxiv"], "max_discoveries": 3}})
    component = load_component("StockArxivHTML")().set(paper_discovery=policy, max_html=0)
    packet = asyncio.run(component.build_tool()).invoke({"query": "Pauli propagation"})
    assert packet["outcome"] == "unavailable" and packet["rows"] == []
    assert packet["backend"] == "arxiv_search_html_fallback"
    assert any("no parseable paper identities" in error for error in packet["errors"])


def test_stock_arxiv_then_stock_url_only_for_observed_hit(monkeypatch):
    from lfx_arxiv.components.arxiv.arxiv import ArXivComponent
    from lfx.components.data_source.url import URLComponent
    calls = []
    def search(self):
        calls.append(("arxiv", self.search_query))
        return DataFrame([{"id": "http://arxiv.org/abs/2601.12345", "title": "A paper", "summary": "Abstract"}])
    async def fetch(self):
        calls.append(("url", tuple(self.urls), self.max_depth, self.format))
        return DataFrame([{"url": self.urls[0], "text": "<html>Paper</html>"}])
    monkeypatch.setattr(ArXivComponent, "search_papers_dataframe", search)
    monkeypatch.setattr(URLComponent, "fetch_content", fetch)
    request = DeepResearchRequest(mode="research", operation_id="stock-1",
        graph_revision={"corpus_id": "papers", "project_id": "research", "corpus_revision": 0, "project_revision": 0},
        questions=({"question_id": "q", "question": "Find a paper"},))
    policy = SimpleNamespace(metadata={"nima_paper_discovery": {"discovery_providers": ["arxiv"], "max_discoveries": 1}})
    component = load_component("StockArxivHTML")().set(payload=request.model_dump(mode="json"), paper_discovery=policy, max_results=5, max_html=1)
    packet = asyncio.run(component.result_data()).data
    assert packet["rows"][0]["title"] == "A paper"
    assert packet["html_pages"][0]["url"] == "https://arxiv.org/html/2601.12345"
    assert calls == [("arxiv", "Find a paper"), ("url", ("https://arxiv.org/html/2601.12345",), 1, "HTML")]


def test_stock_arxiv_outage_does_not_invoke_url(monkeypatch):
    from lfx_arxiv.components.arxiv.arxiv import ArXivComponent
    from lfx.components.data_source.url import URLComponent
    monkeypatch.setattr(ArXivComponent, "search_papers_dataframe", lambda self: DataFrame([{"error": "HTTP 406"}]))
    monkeypatch.setattr("nima_semantica.arxiv_search_fallback.search_page", lambda *args, **kwargs: [])
    async def forbidden(self):
        raise AssertionError("URL must not run without an observed arXiv paper")
    monkeypatch.setattr(URLComponent, "fetch_content", forbidden)
    request = DeepResearchRequest(mode="research", operation_id="stock-2",
        graph_revision={"corpus_id": "papers", "project_id": "research", "corpus_revision": 0, "project_revision": 0},
        questions=({"question_id": "q", "question": "Find a paper"},))
    policy = SimpleNamespace(metadata={"nima_paper_discovery": {"discovery_providers": ["arxiv"], "max_discoveries": 1}})
    packet = asyncio.run(load_component("StockArxivHTML")().set(payload=request.model_dump(mode="json"), paper_discovery=policy).result_data()).data
    assert packet["outcome"] == "unavailable" and not packet["rows"] and not packet["html_pages"]
    assert "HTTP 406" in packet["errors"]


def test_stock_arxiv_exception_is_provider_unavailable_not_pipeline_failure(monkeypatch):
    from lfx_arxiv.components.arxiv.arxiv import ArXivComponent
    from lfx.components.data_source.url import URLComponent
    monkeypatch.setattr(ArXivComponent, "search_papers_dataframe", lambda self: (_ for _ in ()).throw(RuntimeError("HTTP 406")))
    monkeypatch.setattr("nima_semantica.arxiv_search_fallback.search_page", lambda *args, **kwargs: [])
    async def forbidden(self):
        raise AssertionError("URL must not run after arXiv failure")
    monkeypatch.setattr(URLComponent, "fetch_content", forbidden)
    request = DeepResearchRequest(mode="research", operation_id="stock-exception",
        graph_revision={"corpus_id": "papers", "project_id": "research", "corpus_revision": 0, "project_revision": 0},
        questions=({"question_id": "q", "question": "Find a paper"},))
    policy = SimpleNamespace(metadata={"nima_paper_discovery": {"discovery_providers": ["arxiv"], "max_discoveries": 1}})
    packet = asyncio.run(load_component("StockArxivHTML")().set(payload=request.model_dump(mode="json"), paper_discovery=policy).result_data()).data
    assert packet["outcome"] == "unavailable" and packet["errors"] == ["RuntimeError:HTTP 406"]
    assert packet["rows"] == [] and packet["html_pages"] == []


def test_stock_arxiv_denied_without_operator_provider(monkeypatch):
    from lfx_arxiv.components.arxiv.arxiv import ArXivComponent
    monkeypatch.setattr(ArXivComponent, "search_papers_dataframe", lambda self: (_ for _ in ()).throw(AssertionError("unauthorized search")))
    request = DeepResearchRequest(mode="research", operation_id="stock-denied",
        graph_revision={"corpus_id": "papers", "project_id": "research", "corpus_revision": 0, "project_revision": 0},
        questions=({"question_id": "q", "question": "Find a paper"},))
    packet = asyncio.run(load_component("StockArxivHTML")().set(payload=request.model_dump(mode="json")).result_data()).data
    assert packet["outcome"] == "not_authorized"
