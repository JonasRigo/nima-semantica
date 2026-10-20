"""Whole excerpts, continuations, actual prompt budgets and shared owner policy."""
from contextlib import contextmanager
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from nima_semantica.chat_evidence import (ChatDefaults, bounded_history, chat_messages,
    pack_chat_context, prompt_cards, transport_size)
from nima_semantica.installation import Installation, ModelProfile
from nima_semantica.knowledge_chat import answer_chat, retrieve_chat_from_store
from nima_semantica.models import canonical
from nima_semantica.token_budget import TokenCounter
from nima_semantica.tool_contracts import ToolResult


QUERY = "Describe Critical Exponents and Universality of Continuous Phase Transitions"


def configuration(tmp_path, **settings):
    return Installation(data_root=str(tmp_path), llm=ModelProfile(provider="ollama",model="fixture",max_tokens=1024),
        chat=ChatDefaults(**settings))


def region(text, ordinal, *, source="book", project=None):
    key=hashlib.sha256((source+str(ordinal)+str(project)).encode()).hexdigest()
    return dict(region_id=key,source_id=source,text=text,preparation_quality="fast_provisional",retrieval_rank=ordinal,
        evidence=dict(region_id=key,corpus_id="papers",project_id=project,artifact_id="a"*64,content_hash="a"*64,
            source_revision="b"*64,locator=dict(start=ordinal*2000,end=ordinal*2000+len(text),ordinal=ordinal,
                warning="Repeated provenance warning "*30)))


def packet(regions):
    return ToolResult(operation="Retrieve Research Context",status="complete",data=dict(corpus_id="papers",project_id=None,
        graph_revision={},projection_id="fixture",truncated=False,regions=regions,graph_nodes=[],paths=[],
        seeds=[dict(region_id=regions[0]["region_id"],score=1.)] if regions else [],
        traversal=dict(candidate_regions=len(regions),returned_characters=99999)))


def test_introduction_keeps_adjacent_definitions_and_universality(tmp_path):
    intro=region("Critical exponents describe leading power laws at continuous phase transitions. "*12,0)
    definitions=region("The specific heat defines alpha; magnetization defines beta; susceptibility defines gamma. "*10,1)
    universality=region("Universality classes share exponents despite different microscopic interactions. "*10,2)
    incoming=packet([intro,universality,definitions])
    config=configuration(tmp_path,context_tokens=4000)
    result=pack_chat_context(config,QUERY,incoming)
    assert {r["region_id"] for r in result.data["regions"]}=={intro["region_id"],definitions["region_id"],universality["region_id"]}
    assert result.data["traversal"]["estimated_evidence_tokens"]<=4000
    assert all(r["text"]==next(p["text"] for p in incoming.data["regions"] if p["region_id"]==r["region_id"]) for r in result.data["regions"])
    assert incoming.data["traversal"]["returned_characters"]==99999


def test_unequal_passages_skip_oversized_region_without_clipping(tmp_path):
    long=region("Critical exponents "*900,0)
    short=region("Critical exponents define measurable leading powers.",1)
    config=configuration(tmp_path,context_tokens=1024)
    result=pack_chat_context(config,QUERY,packet([long,short]))
    assert [r["text"] for r in result.data["regions"]]==[short["text"]]
    assert result.data["traversal"]["omitted_by_reason"]["evidence_token_budget"]==1
    assert result.data["traversal"]["estimated_evidence_tokens"]<=1024


def test_post_packing_diagnostics_count_actual_evidence_and_prompt(tmp_path):
    config=configuration(tmp_path,context_tokens=1500)
    found=packet([region("Critical exponents "*70,i) for i in range(5)])
    history=[dict(question="previous",answer="bounded answer")]
    result=pack_chat_context(config,QUERY,found,history=history)
    info=result.data["traversal"]
    counter=TokenCounter(config.llm.model)
    assert info["returned_characters"]==sum(len(r["text"]) for r in result.data["regions"])
    assert info["returned_passages"]==len(result.data["regions"])
    assert info["estimated_evidence_tokens"]==counter.value(prompt_cards(result.data))
    assert info["estimated_prompt_tokens"]==counter.messages(chat_messages(QUERY,result.data,history))
    assert info["transport_bytes"]==len(canonical(result.data))
    assert info["candidate_passages"]==info["returned_passages"]+sum(info["omitted_by_reason"].values())
    assert info["estimated_total_tokens"]==info["estimated_prompt_tokens"]+info["reserved_answer_tokens"]+info["context_safety_tokens"]
    assert pack_chat_context(config,QUERY,result,history=history)==result


def test_metadata_transport_limit_is_separate_from_evidence_tokens(tmp_path):
    config=configuration(tmp_path,context_tokens=12000,max_transport_bytes=8192)
    found=packet([region("Critical exponent definition.",i) for i in range(12)])
    result=pack_chat_context(config,QUERY,found)
    assert result.data["traversal"]["transport_bytes"]<=8192
    assert result.data["traversal"]["estimated_evidence_tokens"]<12000
    assert result.data["traversal"]["omitted_by_reason"]["transport_byte_limit"]


def test_generic_retrieval_budget_reports_counts_after_trimming():
    from nima_semantica.research_retrieval import _budget_packet
    original=packet([region("Critical exponents "*70,i) for i in range(8)])
    data=_budget_packet(copy.deepcopy(original.data),1024,8192)
    assert data["traversal"]["returned_characters"]==sum(len(r["text"]) for r in data["regions"])
    assert data["traversal"]["returned_passages"]==len(data["regions"])
    assert data["traversal"]["estimated_evidence_tokens"]<=1024
    assert data["traversal"]["transport_bytes"]==len(canonical(data))<=8192
    assert all(r["text"] in [p["text"] for p in original.data["regions"]] for r in data["regions"])


def test_final_omission_diagnostics_fit_transport_boundary(tmp_path):
    found=packet([region("Critical exponent definition.",i) for i in range(12)])
    baseline=pack_chat_context(configuration(tmp_path),QUERY,found)
    ceiling=baseline.data["traversal"]["transport_bytes"]-128
    config=configuration(tmp_path,max_transport_bytes=ceiling)
    result=pack_chat_context(config,QUERY,found)
    assert len(canonical(result.data))<=ceiling
    assert result.data["traversal"]["transport_bytes"]==len(canonical(result.data))
    assert len(result.data["regions"])<len(baseline.data["regions"])
    assert result.data["traversal"]["omitted_by_reason"]["transport_byte_limit"]


def test_model_capacity_includes_prompt_history_and_reserved_answer(tmp_path):
    config=configuration(tmp_path,context_tokens=12000,history_tokens=1000,answer_tokens=256)
    config.llm=ModelProfile(provider="ollama",model="fixture",max_tokens=256,context_window=4096)
    history=[dict(question="prior",answer="history "*100)]
    candidates=packet([region("Critical exponents "*180,i) for i in range(10)])
    candidates.data["seeds"]=[dict(region_id=r["region_id"]) for r in candidates.data["regions"]]
    result=pack_chat_context(config,QUERY,candidates,history=history)
    info=result.data["traversal"]
    assert info["model_context_tokens"]==4096
    assert info["estimated_history_tokens"]>0 and info["reserved_answer_tokens"]==256
    assert info["estimated_total_tokens"]<=4096
    assert info["omitted_by_reason"]["model_context_capacity"]
    assert len(result.data["regions"])<10


def test_operator_ceilings_are_checked_before_retrieval_or_answer(tmp_path):
    config=configuration(tmp_path,context_tokens=4000)
    found=packet([region("Critical exponent definition.",0)])
    with pytest.raises(ValueError,match="operator chat limits"):
        pack_chat_context(config,QUERY,found,context_tokens=4001)
    with pytest.raises(ValueError,match="operator chat limits"):
        answer_chat(config,QUERY,found,answer_tokens=1025,model=object())


def test_continuations_do_not_cross_source_or_project_boundaries(tmp_path):
    intro=region("Critical exponent introduction.",0,source="one")
    following=region("Definition continues here.",1,source="one")
    foreign=region("Unrelated private text.",1,source="other",project="foreign")
    # Scope filtering belongs to the retrieval boundary; selection only connects
    # consecutive ordinals within the same source and visibility scope.
    config=configuration(tmp_path)
    with pytest.raises(ValueError,match="scope"):
        pack_chat_context(config,"Critical exponent",packet([intro,foreign,following]))
    result=pack_chat_context(config,"Critical exponent",packet([intro,following]))
    for r in result.data["regions"]:
        assert r["evidence"]["project_id"] is None
        assert r["evidence"]==next(p["evidence"] for p in (intro,following) if p["region_id"]==r["region_id"])


def test_answer_sees_multiple_exact_excerpts_and_returns_original_citations(tmp_path):
    config=configuration(tmp_path)
    rows=[region("Critical exponents introduction.",0),region("Exponent definitions follow.",1)]
    class Model:
        def invoke(self,messages):
            evidence=json.loads(messages[1][1])["evidence"]
            assert {e["text"] for e in evidence}=={r["text"] for r in rows}
            assert all("reference" not in e for e in evidence)
            return SimpleNamespace(content="The introduction [S1] is followed by definitions [S2].")
    answer=answer_chat(config,QUERY,packet(rows),model=Model())
    assert {c["reference"]["region_id"] for c in answer["citations"]}=={r["region_id"] for r in rows}
    assert all(c["reference"]==next(r["evidence"] for r in rows if r["region_id"]==c["reference"]["region_id"]) for c in answer["citations"])
    assert answer["retrieval"]["traversal"]["returned_passages"]==2


def test_optional_local_tokenizer_and_explicit_fallback(monkeypatch):
    import nima_semantica.token_budget as tokens
    class Encoding:
        def encode(self,text,**kwargs):return text.split()
    pytest.importorskip("tiktoken")
    monkeypatch.setattr(tokens,"_local_encoding",lambda name:Encoding())
    counter=tokens.TokenCounter("gpt-4o")
    assert counter.name.startswith("tiktoken:") and counter.text("one two three")==3
    fallback=tokens.TokenCounter(conservative=True)
    assert fallback.name=="conservative_utf8_bytes_div2" and fallback.text("αβ")==2


def test_fallback_works_without_optional_tokenizer(monkeypatch):
    import builtins
    original=builtins.__import__
    def offline(name,*args,**kwargs):
        if name=="tiktoken" or name.startswith("tiktoken."):
            raise ModuleNotFoundError("Optional tokenizer is absent")
        return original(name,*args,**kwargs)
    monkeypatch.setattr(builtins,"__import__",offline)
    counter=TokenCounter("gpt-4o")
    assert counter.name=="conservative_utf8_bytes_div2"
    assert counter.text("one two three")==7


def test_missing_or_corrupt_tokenizer_cache_never_downloads(tmp_path,monkeypatch):
    tiktoken=pytest.importorskip("tiktoken")
    from tiktoken import registry
    from nima_semantica.token_budget import _local_encoding
    monkeypatch.setattr(registry,"ENCODINGS",{})
    monkeypatch.setenv("TIKTOKEN_CACHE_DIR",str(tmp_path))
    monkeypatch.setattr(tiktoken,"get_encoding",lambda name:pytest.fail("Unverified cache must not initialize a tokenizer"))
    assert _local_encoding("cl100k_base") is None
    url="https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken"
    (tmp_path/hashlib.sha1(url.encode()).hexdigest()).write_text("corrupt vocabulary")
    assert _local_encoding("cl100k_base") is None


def test_owner_and_terminal_share_selection_settings_and_model_context(store,tmp_path,monkeypatch):
    pytest.importorskip("lfx")
    import sys
    from nima_semantica.cli import register_corpus
    from nima_semantica.source_tools import PrepareSourcesRequest,SourceToolContext,prepare_sources,embed_sources,project_sources
    register_corpus(store,"papers")
    ctx=SourceToolContext(corpus_id="papers",project_id="test",allow_corpus_writes=True)
    text="Critical exponents introduce power laws.\n\n"+"Definition of the order parameter exponent beta. "*60+"\n\nUniversality connects different microscopic systems. "*30
    incoming=prepare_sources(store,PrepareSourcesRequest(mode="prepare_index",operation_id="chat-parity",sources=[dict(name="book.md",text=text)]),ctx)
    assert project_sources(store,embed_sources(store,incoming,ctx),ctx).data["index_ready"]
    config=configuration(tmp_path,context_tokens=4000)
    config.chat=config.chat.model_copy(update={"retrieval":config.chat.retrieval.model_copy(update={"candidate_seeds":9})})
    terminal=retrieve_chat_from_store(config,QUERY,store,corpus_id="papers",project_id="test",knowledge="sources")
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"deploy"))
    from build_tool_guide import load_component
    cls=load_component("AnswerKnowledge");module=sys.modules[cls.__module__]
    @contextmanager
    def access(**kwargs):yield store
    monkeypatch.setattr(module,"configured_store",access)
    class Model:
        _nima_profile=config.llm
        def bind(self,**kwargs):return self
        def invoke(self,messages):
            self.evidence=json.loads(messages[1][1])["evidence"]
            return SimpleNamespace(content="Definitions and universality are supported [S1].")
    model=Model()
    component=cls().set(corpus_id="papers",project_id="test",chat_defaults_json=config.chat.model_dump_json(),
        allow_model_calls=True,model=model,mcp_request=json.dumps(dict(query=QUERY,knowledge="sources",mode="lexical")))
    result=component.run_sync()
    assert model.evidence==prompt_cards(terminal.data)
    assert result["data"]["retrieval"]["traversal"]==terminal.data["traversal"]
    component.set(mcp_request=json.dumps(dict(query=QUERY,knowledge="sources",mode="lexical",context_tokens=4001)))
    with pytest.raises(ValueError,match="operator chat limits"):component.run_sync()
