"""Bounded retrieval and one answer call using the operator's model connection."""
import json
from types import SimpleNamespace
from lfx.io import StrInput,BoolInput,HandleInput
from nima_semantica.component_contracts import manifest_for_component
from nima_semantica.knowledge_chat import KnowledgeAnswerRequest,ChatDefaults,answer_chat,retrieve_chat_from_store
from nima_semantica.chat_evidence import chat_profile,chat_options
from nima_semantica.models import canonical
from nima_semantica.installation import ModelProfile
from nima_semantica.tool_contracts import ToolResult
from nima_semantica.providers import ModelManifest
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
        defaults=ChatDefaults.model_validate_json(self.chat_defaults_json)
        raw=native(self.mcp_request)
        if isinstance(raw,str):
            if len(raw.encode('utf-8'))>defaults.max_transport_bytes:raise ValueError('Question exceeds transport byte limit')
            raw=json.loads(raw)
        elif len(canonical(raw))>defaults.max_transport_bytes:
            raise ValueError('Question exceeds transport byte limit')
        request=KnowledgeAnswerRequest.model_validate(raw)
        budget=request.context_tokens if 'context_tokens' in request.model_fields_set else None
        reserve=request.answer_tokens if 'answer_tokens' in request.model_fields_set else None
        budget,reserve,_=chat_options(defaults,budget,reserve)
        if not self.allow_model_calls or getattr(self,'model',None) is None:raise ValueError('Answer model calls disabled')
        provider=manifest=None
        if request.mode!='lexical' and request.knowledge!='graph':
            if not self.allow_embeddings or not self.allow_attempt_writes:raise ValueError('Query embeddings disabled')
            manifest=ModelManifest.model_validate_json(self.manifest_json)
            provider=EmbeddingProvider(self.embeddings,manifest.model_dump(mode='json'))
        fallback=SimpleNamespace(llm=ModelProfile(provider='compatible',model='operator-connection',base_url='http://operator.invalid/v1',credential=''))
        config=SimpleNamespace(chat=defaults,llm=chat_profile(fallback,self.model))
        selected=self.model.bind(max_tokens=reserve)
        with configured_store(required=False) as store:
            found=retrieve_chat_from_store(config,request.query,store,corpus_id=self.corpus_id,project_id=self.project_id,
                knowledge=request.knowledge,visibility=request.visibility,mode=request.mode,provider=provider,manifest=manifest,
                history=request.history,context_tokens=budget,answer_tokens=reserve,allow_embeddings=self.allow_embeddings,
                allow_attempt_writes=self.allow_attempt_writes,model=selected)
        data=answer_chat(config,request.query,found,history=request.history,context_tokens=budget,answer_tokens=reserve,model=selected)
        return ToolResult(operation='Answer Knowledge',status='complete',data=data,receipt_ids=found.receipt_ids).model_dump(mode='json')
