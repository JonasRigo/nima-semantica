from types import SimpleNamespace
import pytest
from nima_semantica.math_retrieval import MathRetrievalPolicy, RetrieveMathContext, retrieve_math_context
from nima_semantica.mathematics_contracts import FIELDS, contract_issues
from nima_semantica.ontology_profiles import saved_profile
from nima_semantica.ontology_services import OntologyRegistry, validate_delta_ontology
from nima_semantica.okf_contracts import OKFNode, OKFEdge, OKFDelta
from test_source_pipeline import store, pipeline, request, Provider, MANIFEST
from test_research_retrieval import QueryProvider

PROFILE=saved_profile('beyond_iid_mathematics')
def ctx(**kwargs):
    return SimpleNamespace(corpus_id='papers',project_id='research',retrieval=MathRetrievalPolicy(enabled=True,**kwargs))
def node(identifier,kind='statement',**fields):
    mathematics={f:'not_applicable' for f in FIELDS}|{'exact_statement':'For all density operators rho, ...','unresolved':[]}|fields
    if kind=='statement':mathematics['epistemic_kind']='source_statement'
    return OKFNode(node_id=identifier,node_type=kind,corpus_id='papers',project_id='research',properties={'mathematics':mathematics})
def test_internal_hybrid_preserves_exact_evidence_and_graph_packet(store):
    pipeline(store,request(index_mode='vector'),provider=Provider())
    p=QueryProvider()
    result=retrieve_math_context(store,ctx(allow_embeddings=True,allow_attempt_writes=True),RetrieveMathContext(query='Claim',purpose='Check assumptions'),provider=p,manifest=MANIFEST)
    assert result['mode']=='hybrid' and p.calls==['Claim']
    assert result['passages'][0]['text']==request().sources[0].text
    assert result['graph_revision']['project_id']=='research'
    assert 'paths' in result and 'graph_nodes' in result
    assert result['receipt_ids'] and len(result['source_text'])<=12000
    assert result['traversal']['visited_nodes'] >= 1

def test_missing_operator_provider_is_explicit_no_fallback(store):
    pipeline(store)
    with pytest.raises(ValueError,match='operator embedding'):
        retrieve_math_context(store,ctx(),RetrieveMathContext(query='Claim',purpose='Check'))
    result=retrieve_math_context(store,ctx(),RetrieveMathContext(query='Claim',purpose='Explicit retry',mode='lexical'))
    assert result['mode']=='lexical' and not result['receipt_ids']

def test_failed_provider_receipted_without_lexical_fallback(store):
    pipeline(store,request(index_mode='vector'),provider=Provider())
    with pytest.raises(ValueError,match='No lexical fallback'):
        retrieve_math_context(store,ctx(allow_embeddings=True,allow_attempt_writes=True),RetrieveMathContext(query='Claim',purpose='Check'),provider=QueryProvider(error=RuntimeError('provider down')),manifest=MANIFEST)
    assert store.records('ExecutionReceipt',corpus_id='papers',project_id='research')

def test_contract_requires_exact_qualifiers_and_keeps_wishlist_desired():
    n=node('statement');assert not contract_issues(PROFILE,[n],[])
    damaged=node('damaged',domain='unknown')
    assert contract_issues(PROFILE,[damaged],[])
    goal=node('goal','projectrequirement')
    assert contract_issues(PROFILE,[goal],[])
    goal.properties['mathematics']['epistemic_kind']='desired'
    assert not contract_issues(PROFILE,[goal],[])

def test_distinct_conventions_cannot_be_merged():
    a=node('a',normalization='normalized');b=node('b',normalization='subnormalized')
    a.properties['consolidation_variants']=[{'properties':b.properties}]
    assert any('unequal' in x['message'] for x in contract_issues(PROFILE,[a],[]))

def test_dependency_types_prevent_theorem_necessity_from_particular_proof():
    a=node('a');b=node('b')
    e=OKFEdge(edge_id='e',relation='requires',source_id=a.ref,target_id=b.ref,corpus_id='papers',project_id='research')
    delta=OKFDelta(delta_id='d',base_revision={'corpus_id':'papers','project_id':'research','corpus_revision':0,'project_revision':0},corpus_id='papers',project_id='research',ontology_profile=PROFILE.digest,upsert_nodes=(a,b),add_edges=(e,),reason='test')
    report=validate_delta_ontology(delta,OntologyRegistry((PROFILE,)))
    assert not report.valid
    assert any(v.code=='ontology.source_type_mismatch' for v in report.issues)
    assert any(v.code=='ontology.mathematics_contract' for v in report.issues)

def test_cycles_fail_and_legacy_profiles_are_unaffected():
    a=node('a','proofstep');b=node('b','proofstep')
    edges=[OKFEdge(edge_id=i,relation='requires',source_id=s.ref,target_id=t.ref,corpus_id='papers',project_id='research') for i,s,t in [('ab',a,b),('ba',b,a)]]
    assert any('cycle' in v['message'] for v in contract_issues(PROFILE,[a,b],edges))
    assert not contract_issues(saved_profile('literature_review'),[a,b],edges)


def test_private_math_explicit_lexical_read_preserves_graph_observation(store,tmp_path,lexical_installation):
    from nima_semantica.math_session_service import MathSessionService, MathServiceConfig
    from nima_semantica.math_mcp_contracts import invoke
    from test_math_mcp_service import opened
    from test_source_pipeline import context
    _,_,prepared=pipeline(store,ctx=context(project_id='p'))
    config=MathServiceConfig(database_path=str(tmp_path/'private.sqlite'),project_id='p',run_id='r',corpus_id='papers',allow_retrieval=True,retrieval_projection_id=prepared.data['projection']['projection_id'])
    service=MathSessionService(config,research_store=store)
    sid=opened(service)
    result=invoke(service,'retrieve_context',dict(session_id=sid,request_id='source',expected_revision=0,query='Claim evidence',purpose='Exact method',mode='lexical'))
    assert result['status']=='completed',result
    assert result['result']['retrieval_context']['mode']=='lexical'
