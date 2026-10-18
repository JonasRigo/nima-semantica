"""Bounded retrieval and one answer call using the operator's model connection."""
import json,uuid
from types import SimpleNamespace
from lfx.io import StrInput,BoolInput,HandleInput
from nima_semantica.component_contracts import manifest_for_component
from nima_semantica.knowledge_chat import KnowledgeAnswerRequest,ChatDefaults,answer_chat
from nima_semantica.installation import ModelProfile
from nima_semantica.tool_contracts import ToolResult
from nima_semantica.providers import ModelManifest
from nima_semantica.research_retrieval import ResearchRetrievalRequest,ResearchRetrievalContext,retrieve_research_context
from nima_semantica.orchestration.langflow.adapter_support import configured_store,EmbeddingProvider,native
from nima_semantica.orchestration.langflow.stages.base import BlockingStage as BaseComponent

class AnswerKnowledge(BaseComponent):
    name='AnswerKnowledge'
    display_name='Answer Knowledge'
    description='Current corpus/project evidence, bounded graph expansion, exact citations, one answer call. No scientific graph admission.'
    nima_manifest=manifest_for_component('AnswerKnowledge')
    inputs=[HandleInput(name='mcp_request',display_name='Question JSON',input_types=['Message','Data','JSON'],required=True),
        StrInput(name='corpus_id',display_name='Authorized corpus',value='papers'),
        StrInput(name='project_id',display_name='Authorized project',value='research'),
        BoolInput(name='allow_model_calls',display_name='Allow answer call',value=False),
        BoolInput(name='allow_embeddings',display_name='Allow query embeddings',value=False),
        BoolInput(name='allow_attempt_writes',display_name='Allow query receipts',value=False),
        StrInput(name='chat_defaults_json',display_name='Chat limits',value='{}'),
        StrInput(name='manifest_json',display_name='Embedding manifest',value='{}'),
        HandleInput(name='embeddings',display_name='Query embedding model',input_types=['Embeddings'],required=False),
        HandleInput(name='model',display_name='Answer model',input_types=['LanguageModel'],required=False)]
    def run_sync(self):
        raw=native(self.mcp_request)
        if isinstance(raw,str):
            if len(raw)>100000:raise ValueError('Question exceeds transport limit')
            raw=json.loads(raw)
        request=KnowledgeAnswerRequest.model_validate(raw)
        defaults=ChatDefaults.model_validate_json(self.chat_defaults_json)
        if request.context_tokens>defaults.context_tokens or request.answer_tokens>defaults.answer_tokens:raise ValueError('Question exceeds operator chat limits')
        if not self.allow_model_calls or getattr(self,'model',None) is None:raise ValueError('Answer model calls disabled')
        provider=manifest=None
        if request.mode!='lexical':
            if not self.allow_embeddings or not self.allow_attempt_writes:raise ValueError('Query embeddings disabled')
            manifest=ModelManifest.model_validate_json(self.manifest_json)
            provider=EmbeddingProvider(self.embeddings,manifest.model_dump(mode='json'))
        query=ResearchRetrievalRequest(query=request.query,knowledge=request.knowledge,visibility=request.visibility,mode=request.mode,
            max_context_tokens=request.context_tokens,limit=4,max_results=6,max_hops=2,max_nodes=24,max_edges=48,max_chars=16000,
            operation_id='chat-'+uuid.uuid4().hex if request.mode!='lexical' else None)
        context=ResearchRetrievalContext(corpus_id=self.corpus_id,project_id=self.project_id,allow_embeddings=self.allow_embeddings,allow_attempt_writes=self.allow_attempt_writes)
        with configured_store(required=False) as store:found=retrieve_research_context(store,query,context,provider=provider,manifest=manifest)
        config=SimpleNamespace(chat=defaults,llm=ModelProfile(provider='ollama',model='operator-connection'))
        data=answer_chat(config,request.query,found,history=request.history,answer_tokens=request.answer_tokens,model=self.model.bind(max_tokens=request.answer_tokens))
        return ToolResult(operation='Answer Knowledge',status='complete',data=data,receipt_ids=found.receipt_ids).model_dump(mode='json')
