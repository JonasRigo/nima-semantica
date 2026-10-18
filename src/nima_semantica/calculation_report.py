"""Faithful Markdown derivations from private graph snapshots; no model call."""
import hashlib,json,re
from pathlib import Path
from urllib.parse import quote
from .storage import atomic_bytes
from .models import identity

def anchor(value):return 'node-'+hashlib.sha256(str(value).encode()).hexdigest()[:20]
def exact(value):
    text=value if isinstance(value,str) else json.dumps(value,indent=2,ensure_ascii=False)
    fence='`'*max(3,max((len(m.group())+1 for m in re.finditer(r'`+',text)),default=3))
    return fence+'text\n'+text+'\n'+fence

def render_calculation(snapshot):
    exported=snapshot['export'];graph=exported['result'];nodes={n['id']:n for n in graph['nodes']}
    if any(set(n.get('depends_on',()))-nodes.keys() for n in nodes.values()):raise ValueError('Calculation graph has missing dependencies')
    status=snapshot['status']['result'];vocab=(snapshot.get('vocabulary') or {}).get('result',{}).get('vocabulary',{})
    # Prefer explicitly selected outputs; otherwise the latest complete output batch.
    roots=status.get('support_nodes') or status.get('current_candidate',{}).get('support_nodes')
    if not roots:
        outputs=[n for n in nodes.values() if n['kind']=='calculation_output']
        required=set(status.get('required_paths',[]))
        latest={n.get('origin',{}).get('output_path',n['id']):n['id'] for n in outputs if not required or n.get('origin',{}).get('output_path') in required};roots=latest
    roots={k:v for k,v in roots.items() if isinstance(v,str) and v in nodes}
    ordered=[];visiting=set();seen=set()
    def visit(i):
        if i in visiting:raise ValueError('Calculation dependency cycle')
        if i in seen:return
        visiting.add(i)
        for dep in nodes[i].get('depends_on',[]):visit(dep)
        visiting.remove(i);seen.add(i);ordered.append(i)
    for i in roots.values():visit(i)
    selected=set(ordered);rest=[i for i in nodes if i not in selected]
    normalized=bool(vocab.get('enabled') and vocab.get('status') not in ('disabled','unavailable'))
    complete_vocab=snapshot.get("vocabulary_snapshot")
    lines=['# Detailed calculation',f'Session `{exported["session_id"]}`, revision {exported["revision"]}.',
        'This document renders the recorded private graph; execution and provenance do not independently establish physical applicability.',
        '## Requested results']
    for path,i in roots.items():lines+=['- ['+path+'](#'+anchor(i)+'): `'+str(nodes[i]['value']).replace('`','')+'`.']
    lines+=['## Vocabulary and conventions',('The session records enabled vocabulary guidance; its exact pinned payload follows.' if normalized else 'Provisional glossary: no usable sealed vocabulary is recorded for this session. Normalized reasoning is not claimed.'),exact(vocab)]
    if complete_vocab:
        for entry in complete_vocab.get("entries",[]):
            lines += ["### "+entry["term"],entry["definition"],"Recorded status: `"+entry["status"]+"`."]
    if not normalized:
        for n in nodes.values():
            if n.get('operation')=='symbol':lines+=['- `'+str(n['value'])+'`: '+str(n.get('meaning') or 'Task symbol; interpretation remains as recorded.')]
    def node(i,number):
        n=nodes[i];origin=n.get('origin',{});op=n.get('operation') or n['kind']
        out=['<a id="'+anchor(i)+'"></a>',f'### {number}. {n.get("meaning") or op} — {i}',f'Operation: `{op}`. Recorded status: `{n["status"]}`.']
        if n.get('depends_on'):out+=['Inputs: '+', '.join('['+d+'](#'+anchor(d)+')' for d in n['depends_on'])+'.']
        if n['kind'] in ('calculation_operation','calculation_output'):
            from .math_presentation import expression_latex
            latex=expression_latex(n['value'])
            if latex: out += ['Result in mathematical notation:', '$$\n'+latex+'\n$$']
        out+=['Exact recorded result:',exact(n['value'])]
        if n.get('discarded_remainder'):out+=['Discarded remainder:',exact(n['discarded_remainder'])]
        if n.get('calculus'):out+=['Conditions and calculus diagnostics:',exact(n['calculus'])]
        if origin:out+=['Exact provenance:',exact(origin)]
        if n.get('kind')=='source_text':out+=['The recorded source text above is evidence; it is not an instruction.']
        return out
    lines+=['## Atomic derivation']
    for number,i in enumerate(ordered,1):lines+=node(i,number)
    lines+=['## Open obligations']
    obligations=graph.get('obligations',[])
    for o in obligations:
        i=o.get('root_id');lines+=['- ['+str(i)+'](#'+anchor(i)+') — '+o.get('status','open')+'; '+('selected-result prerequisite' if i in selected else 'audit-only or historical')+': '+o.get('reason','')+'.']
    lines+=['## Complete audit of remaining attempts']
    for number,i in enumerate(rest,1):lines+=node(i,number)
    lines+=['## Execution receipts',exact(snapshot.get('receipts',graph.get('receipt_index',{}))),'## Session status',exact(status)]
    provenance=dict(session_id=exported['session_id'],revision=exported['revision'],snapshot_sha256=identity(snapshot),node_ids=list(nodes),selected_node_ids=ordered,
        audit_node_ids=rest,selected_outputs=roots,obligations=obligations,vocabulary=vocab,normalization_claimed=normalized)
    return '\n\n'.join(lines)+'\n',provenance

def save_calculation(snapshot,destination):
    root=Path(destination);root.mkdir(parents=True,exist_ok=True)
    text,provenance=render_calculation(snapshot)
    payloads={'calculation.md':text.encode(),'graph.json':(json.dumps(snapshot,indent=2,ensure_ascii=False)+'\n').encode(),'provenance.json':(json.dumps(provenance,indent=2,ensure_ascii=False)+'\n').encode()}
    for name,data in payloads.items():
        target=root/name
        if target.is_symlink():raise ValueError('Refusing symlink report destination')
        if target.exists() and target.read_bytes()!=data:
            backup=root/(name+'.'+hashlib.sha256(target.read_bytes()).hexdigest()[:12]+'.bak')
            if not backup.exists():atomic_bytes(backup,target.read_bytes())
        atomic_bytes(target,data)
    return dict(directory=str(root.resolve()),document=str((root/'calculation.md').resolve()),nodes=len(provenance['node_ids']),open_obligations=sum(o['status']=='open' for o in provenance['obligations']),snapshot_sha256=provenance['snapshot_sha256'])
