"""Regression for preparation against unrelated project-local ontologies."""
import pytest
from contextlib import closing
from nima_semantica.storage import GraphStore
from nima_semantica.corpus_registry import CorpusRegistry
from nima_semantica.registry_contracts import CorpusDescriptor
from nima_semantica.ontology_profiles import OntologyProfile,NodeType,RelationType
from nima_semantica.ontology_tools import save_ontology,SaveOntologyRequest,OntologyContext
from nima_semantica.ontology_services import OntologyService
from nima_semantica.graph_commit import commit_okf_delta,OKFCommitApproval
from nima_semantica.okf_contracts import OKFDelta,OKFNode,OKFEdge
from nima_semantica.project_update import prepare_update,UpdateProjectRequest,UpdateProjectContext

@pytest.mark.parametrize('foreign_corpus,foreign_project',[('foreign','research'),('papers','other')])
def test_prepare_ignores_foreign_project_ontology(tmp_path,foreign_corpus,foreign_project):
    with closing(GraphStore(tmp_path/'store')) as store:
        for corpus in {'papers',foreign_corpus}:
            CorpusRegistry(store).register_corpus(CorpusDescriptor(corpus_id=corpus,name=corpus,corpus_revision='1'))
        scope=dict(corpus_id=foreign_corpus,project_id=foreign_project)
        profile=OntologyProfile(name='foreign_private',version='1.0.0',node_types=(NodeType(name='special',description='Foreign-only type'),),relation_types=(RelationType(name='foreign_relation',source_types=('special',),target_types=('special',),description='Foreign-only relation'),))
        result=save_ontology(store,SaveOntologyRequest(mode='save',operation_id='foreign-profile',profile=profile.model_dump(mode='json')),OntologyContext(**scope,allow_writes=True))
        assert result.status=='complete'
        a=OKFNode(node_id='a',node_type='special',ontology_profile=profile.digest,**scope)
        b=OKFNode(node_id='b',node_type='special',ontology_profile=profile.digest,**scope)
        edge=OKFEdge(edge_id='ab',relation='foreign_relation',source_id=a.ref,target_id=b.ref,ontology_profile=profile.digest,**scope)
        delta=OKFDelta(delta_id='foreign-graph',base_revision=store.graph_revision(**scope),ontology_profile=profile.digest,upsert_nodes=(a,b),add_edges=(edge,),reason='Fixture foreign graph',**scope)
        commit_okf_delta(store,delta,OKFCommitApproval.for_delta(delta,approval_id='fixture-approval',approved_by='test',rationale='Fixture exact delta'),ontology=OntologyService.from_store(store,**scope))
        local=dict(corpus_id='papers',project_id='research')
        proposed=OKFDelta(delta_id='local-proposal',base_revision=store.graph_revision(**local),ontology_profile='claim_obligation',upsert_nodes=(OKFNode(node_id='local',node_type='claim',**local),),reason='Local-only proposal',**local)
        before=store.revision
        prepared=prepare_update(store,UpdateProjectRequest(mode='prepare',operation_id='local-proposal',graph_revision=proposed.base_revision,delta=proposed),UpdateProjectContext(**local))
        assert prepared==proposed
        assert store.revision==before
        assert store.read_okf_snapshot(**scope).nodes==(a,b)
