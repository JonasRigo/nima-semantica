"""Build the single-call knowledge answer canvas; no publication."""
import copy,json,hashlib
from build_tool_guide import ROOT,COMPONENTS,load_component

def build():
    guide=json.loads((ROOT/'examples/langflow_replacement/tool_guide.json').read_text())
    nodes=[]
    for name,x in [('ChatInput',0),('ChatOutput',840)]:
        node=copy.deepcopy(next(n for n in guide['data']['nodes'] if n['data']['type']==name))
        nid=name+'-answer';node.update(id=nid,position={'x':x,'y':0});node['data']['id']=nid
        if name=='ChatInput':node['data']['node']['template']['input_value']['value']='{}'
        nodes.append(node)
    name='AnswerKnowledge';component=load_component(name)();node=component.to_frontend_node();nid=name+'-nima'
    node.update(id=nid,type='genericNode',position={'x':420,'y':0});node['data'].update(id=nid,type=name)
    source=(COMPONENTS/(name+'.py')).read_text();node['data']['node']['template']['code']['value']=source
    metadata={'nima_component_id':'answer_knowledge','contract_version':'1','source_sha256':hashlib.sha256(source.encode()).hexdigest()}
    node['data']['node']['metadata']=metadata;nodes.append(node);by={n['id']:n for n in nodes};edges=[]
    for left,output,right,field in [('ChatInput-answer','message',nid,'mcp_request'),(nid,'preview','ChatOutput-answer','input_value')]:
        a,b=by[left],by[right];port=next(p for p in a['data']['node']['outputs'] if p['name']==output);inp=b['data']['node']['template'][field]
        sh={'dataType':a['data']['type'],'id':left,'name':output,'output_types':port['types']};th={'fieldName':field,'id':right,'inputTypes':inp.get('input_types',[]),'type':inp['type']}
        edges.append({'id':left+'-'+right,'source':left,'target':right,'sourceHandle':json.dumps(sh).replace('"','œ'),'targetHandle':json.dumps(th).replace('"','œ'),'data':{'sourceHandle':sh,'targetHandle':th}})
    return {'name':'Answer Knowledge','is_component':False,'endpoint_name':'nima-answer-knowledge','description':'Bounded source/graph retrieval and one citation-checked answer call.',
        'data':{'nodes':nodes,'edges':edges,'viewport':{'x':0,'y':0,'zoom':0.8}},'nima_tool_manifest':{'tool_id':'answer_knowledge','contract_version':'1','visual_approval':'not_ready','publication':'not_published_by_builder','components':{'AnswerKnowledge':metadata}}}
if __name__=='__main__':print(json.dumps(build(),indent=2,ensure_ascii=False))
