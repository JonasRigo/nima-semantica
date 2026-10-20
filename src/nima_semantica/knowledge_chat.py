"""One bounded retrieval and one answer call, shared by terminal and harness chat."""
from contextlib import closing
import json,re,uuid
from typing import Literal
from pydantic import Field
from .models import StrictModel,ConflictError
from .research_retrieval import ResearchRetrievalRequest,ResearchRetrievalContext,retrieve_research_context

from .chat_evidence import (ChatDefaults, evidence_cards as packet_cards, pack_chat_context,
    chat_messages, bounded_history, chat_options, chat_profile)
from .token_budget import TokenCounter


def chat_retrieval_request(config, query, *, knowledge, visibility, mode):
    if len(query)>8000:
        raise ValueError("Chat question exceeds the 8000-character limit")
    limits = config.chat.retrieval
    return ResearchRetrievalRequest(query=query, mode=mode, knowledge=knowledge, visibility=visibility,
        limit=limits.candidate_seeds, max_results=limits.candidate_regions, max_hops=limits.max_hops,
        max_nodes=limits.max_nodes, max_edges=limits.max_edges, max_chars=limits.candidate_characters,
        max_context_tokens=None, max_transport_bytes=config.chat.max_transport_bytes,
        operation_id='chat-'+uuid.uuid4().hex if mode!='lexical' else None)


def retrieve_chat_from_store(config, query, store, *, corpus_id, project_id=None, knowledge='both', visibility=None,
        mode='lexical', provider=None, manifest=None, history=(), context_tokens=None, answer_tokens=None,
        allow_embeddings=False, allow_attempt_writes=False, model=None):
    chat_options(config.chat, context_tokens, answer_tokens)
    request = chat_retrieval_request(config, query, knowledge=knowledge,
        visibility=visibility or ('both' if project_id else 'corpus'), mode=mode)
    context = ResearchRetrievalContext(corpus_id=corpus_id, project_id=project_id,
        allow_embeddings=allow_embeddings, allow_attempt_writes=allow_attempt_writes)
    result = retrieve_research_context(store, request, context, provider=provider, manifest=manifest)
    return pack_chat_context(config, query, result, history=history, context_tokens=context_tokens,
        answer_tokens=answer_tokens, model=model)

def retrieve_chat(config,query,*,corpus_id,project_id=None,knowledge='both',visibility=None,context_tokens=None,mode=None,history=(),answer_tokens=None):
    from .installation import store_path,project_binding
    from .corpus_snapshot import CorpusSnapshot
    from .storage import GraphStore
    from .setup_services import embedding_provider
    visibility=visibility or ('both' if project_id else 'corpus')
    if visibility=='project' and not project_id:raise ValueError('Project-only chat requires a project')
    selected_mode=mode or ('hybrid' if config.embedding and knowledge!='graph' else 'lexical')
    chat_options(config.chat, context_tokens, answer_tokens)
    request=chat_retrieval_request(config,query,knowledge=knowledge,visibility=visibility,mode=selected_mode)
    context=ResearchRetrievalContext(corpus_id=corpus_id,project_id=project_id,allow_embeddings=selected_mode!='lexical',allow_attempt_writes=selected_mode!='lexical')
    if selected_mode=='lexical':
        with CorpusSnapshot(store_path(config),corpus_id=corpus_id,project_id=project_id) as store:
            result=retrieve_research_context(store,request,context)
            return pack_chat_context(config,query,result,history=history,context_tokens=context_tokens,answer_tokens=answer_tokens)
    try:store=GraphStore(store_path(config))
    except ConflictError as error:
        if str(error)!='another service owns this corpus':raise
        from .owner_operations import call_owner
        if project_id:
            binding=project_binding(config,corpus_id,project_id)
        else:
            from .installation import list_projects
            candidates=[p for p in list_projects(config,corpus_id) if p.get('mcp_url')]
            if not candidates:raise ValueError('An active corpus owner requires a registered owner connection for vector chat') from error
            binding=project_binding(config,corpus_id,candidates[0]['project_id'])
        result=call_owner(binding['mcp_url'],'Retrieve Research Context',request.model_dump(mode='json'),timeout=config.retrieval_limits.owner_timeout_seconds)
        if result.status not in ('complete','partial'):raise ValueError('Corpus owner retrieval failed: '+str(result.diagnostics))
        if (result.data['corpus_id'],result.data['project_id'])!=(corpus_id,binding['project_id']):raise ValueError('Corpus owner returned foreign scope')
        return pack_chat_context(config,query,result,history=history,context_tokens=context_tokens,answer_tokens=answer_tokens)
    with closing(store):
        provider,manifest=embedding_provider(config)
        result=retrieve_research_context(store,request,context,provider=provider,manifest=manifest)
        return pack_chat_context(config,query,result,history=history,context_tokens=context_tokens,answer_tokens=answer_tokens)

def evidence_cards(result):
    if result.status not in ('complete','partial'):raise ValueError('Retrieval is not ready: '+str(result.diagnostics))
    return packet_cards(result.data)

def answer_chat(config,query,result,*,history=(),context_tokens=None,answer_tokens=None,history_tokens=None,model=None):
    result=pack_chat_context(config,query,result,history=history,context_tokens=context_tokens,answer_tokens=answer_tokens,history_tokens=history_tokens,model=model)
    cards=evidence_cards(result);allowed={c['citation'] for c in cards}
    actual_profile=chat_profile(config,model)
    counter=TokenCounter(actual_profile.model,tokenizer_model=config.chat.tokenizer_model)
    cap=config.chat.history_tokens if history_tokens is None else history_tokens
    bounded=bounded_history(history,cap,counter)
    from .model_setup import native_chat_model
    profile=actual_profile.model_copy(update={'max_tokens':answer_tokens or config.chat.answer_tokens})
    selected=model or native_chat_model(profile)
    response=selected.invoke(chat_messages(query,result.data,bounded))
    answer=response.content if isinstance(response.content,str) else '\n'.join(b.get('text','') for b in response.content if isinstance(b,dict))
    used=set(re.findall(r'\[((?:S|G)\d+)\]',answer))
    if used-allowed:raise ValueError('Answer cited unknown evidence identifiers')
    if cards and not used:raise ValueError('Answer did not cite its retrieved evidence')
    return dict(answer=answer,citations=[c for c in cards if c['citation'] in used],retrieval=dict(scope={k:result.data[k] for k in ('corpus_id','project_id')},graph_revision=result.data['graph_revision'],projection_id=result.data['projection_id'],traversal=result.data['traversal'],truncated=result.data['truncated']),
        usage=getattr(response,'usage_metadata',None),model_calls=1,context_estimator=result.data['traversal']['token_count_method'],answer_token_limit=profile.max_tokens)


class KnowledgeAnswerRequest(StrictModel):
    query:str=Field(min_length=1,max_length=8000)
    knowledge:Literal['sources','graph','both']='both'
    visibility:Literal['corpus','project','both']='both'
    mode:Literal['lexical','vector','hybrid']='hybrid'
    context_tokens:int=Field(default=12000,ge=1024,le=100000)
    answer_tokens:int=Field(default=1024,ge=64,le=16000)
    history:tuple[dict,...]=Field(default=(),max_length=100)


def answer_through_owner(config,query,*,corpus_id,project_id=None,knowledge='both',visibility=None,context_tokens=None,answer_tokens=None,mode=None,history=()):
    budget,reserve,_=chat_options(config.chat,context_tokens,answer_tokens)
    history=bounded_history(history,config.chat.history_tokens,TokenCounter(config.llm.model,tokenizer_model=config.chat.tokenizer_model))
    from .installation import project_binding,list_projects
    from .owner_operations import call_owner
    if project_id:binding=project_binding(config,corpus_id,project_id)
    else:
        candidates=[p for p in list_projects(config,corpus_id) if p.get('mcp_url')]
        if not candidates:raise ValueError('No configured corpus owner for answering; set the model credential in the terminal environment')
        binding=project_binding(config,corpus_id,candidates[0]['project_id'])
    request=KnowledgeAnswerRequest(query=query,knowledge=knowledge,visibility=visibility or ('both' if project_id else 'corpus'),
        context_tokens=budget,answer_tokens=reserve,
        mode=mode or ('hybrid' if config.embedding and knowledge!='graph' else 'lexical'),history=tuple(history))
    result=call_owner(binding['mcp_url'],'Answer Knowledge',request.model_dump(mode='json'),timeout=config.retrieval_limits.owner_timeout_seconds)
    if result.status!='complete':raise ValueError('Knowledge answer failed: '+str(result.diagnostics))
    if result.data['retrieval']['scope']!={'corpus_id':corpus_id,'project_id':binding['project_id']}:raise ValueError('Knowledge answer returned foreign scope')
    return result.data
