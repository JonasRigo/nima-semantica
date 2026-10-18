"""Calculus semantics, retained conditions and exact-reuse regressions."""
import contextlib
import io
import json
import pytest
from nima_semantica.math_graph_program import compile_graph_program
from nima_semantica.math_single_graph_state import SingleCalculationGraph
from nima_semantica.math_calculation_assistance import expand_reuse
from nima_semantica.math_interface_helpers import bind_input_origins
from nima_semantica.math_mcp_contracts import AtomicStep

def step(id, op, args=(), **kw):
    return dict(id=id,op=op,args=list(args),meaning=id,**kw)

def run(steps, out='answer', graph=None, receipt='calculus'):
    graph=graph or SingleCalculationGraph('Calculate x epsilon a',(),('answer',))
    request,_=bind_input_origins(graph,dict(steps=steps,outputs={'answer':out}))
    plan=compile_graph_program(graph,request['steps'],request['outputs'])
    plan['typed_applicability']=True
    buf=io.StringIO()
    with contextlib.redirect_stdout(buf): exec(plan['source'],{})
    observed=graph.record_compiled_execution(plan,dict(outcome='executed',exit_code=0,stdout=buf.getvalue()),receipt)
    return graph,plan,observed,json.loads(buf.getvalue())

def inputs():
    return [step('x','symbol',value='x'),step('zero','integer',value=0),step('one','integer',value=1)]

def test_integral_bounds_and_exact_reuse():
    g,p,r,v=run(inputs()+[step('answer','integrate',['x','x','zero','one'])])
    assert v['outputs']['answer']=='1/2'
    args,_=expand_reuse(g,dict(steps=[],outputs={'answer':'old'},reuse={'old':r['outputs']['answer']['id']}))
    assert any(s['op']=='integrate' and len(s['args'])==4 for s in args['steps'])
    rp=compile_graph_program(g,args['steps'],args['outputs'],step_limit=512)
    buf=io.StringIO()
    with contextlib.redirect_stdout(buf):exec(rp['source'],{})
    assert json.loads(buf.getvalue())['outputs']['answer']=='1/2'

@pytest.mark.parametrize('direction, expected',[('+','oo'),('-','-oo')])
def test_directional_limit_and_divergence(direction,expected):
    g,p,r,v=run(inputs()+[step('inv','divide',['one','x']),step('answer','limit',['inv','x','zero'],options={'direction':direction})])
    assert v['outputs']['answer']==expected
    assert r['steps']['answer']['id'] in g.obligations

def test_laurent_series_and_explicit_remainder():
    steps=inputs()+[step('gam','gamma',['x']),step('laurent','series',['gam','x','zero','one'],options={'direction':'+'}),step('answer','truncate_series',['laurent'])]
    g,p,r,v=run(steps)
    assert '1/x' in v['steps']['laurent'] and 'O(x)' in v['steps']['laurent']
    assert r['steps']['answer']['discarded_remainder']=='O(x)'
    assert r['steps']['answer']['id'] in g.obligations

def test_improper_integral_retains_conditions():
    steps=inputs()+[step('a','symbol',value='a',symbol_assumptions={'positive':True}),step('epsilon','symbol',value='epsilon',symbol_assumptions={'positive':True}),step('two','add',['one','one']),step('exponent','subtract',['one','epsilon']),step('num','power',['x','exponent']),step('denbase','add',['x','a']),step('den','power',['denbase','two']),step('f','divide',['num','den']),step('inf','infinity'),step('answer','integrate',['f','x','zero','inf'])]
    g,p,r,v=run(steps)
    assert 'Piecewise' in v['outputs']['answer']
    assert r['steps']['answer']['calculus']['conditional']
    assert r['steps']['answer']['id'] in g.obligations
    assert r['steps']['a']['id'] in g.obligations
    assert r['steps']['inf']['id'] not in g.obligations

@pytest.mark.parametrize('bad',[
    step('answer','integrate',['x','x','zero']),
    step('answer','limit',['x','x','zero']),
    step('answer','series',['x','x','zero','one'],options={'direction':'sideways'}),
    step('answer','exp',['x'],options={'direction':'+'}),
    step('answer','symbol',value='x',symbol_assumptions={'__class__':True}),
    step('answer','symbol',value='x',symbol_assumptions={'positive':True,'negative':True}),
    step('answer','integrate',['x','x'],value='42'),
])
def test_invalid_calculus_requests_rejected(bad):
    g=SingleCalculationGraph('x',(),())
    with pytest.raises(ValueError):compile_graph_program(g,inputs()+[bad],{'answer':'answer'})

def test_contract_accepts_calculus_and_preserves_options():
    a=AtomicStep.model_validate(step('answer','limit',['expr','x','zero'],options={'direction':'-'}))
    assert a.options=={'direction':'-'}

def test_indefinite_integral_and_transcendentals():
    g,p,r,v=run(inputs()+[step('ex','exp',['x']),step('answer','integrate',['ex','x'])])
    assert v['outputs']['answer']=='exp(x)'

def test_series_order_is_bounded_in_worker():
    g=SingleCalculationGraph('x',(),())
    plan=compile_graph_program(g,inputs()+[step('n','integer',value=21),step('answer','series',['x','x','zero','n'],options={'direction':'+'})],{'answer':'answer'})
    with pytest.raises(ValueError,match='series order'):exec(plan['source'],{})
