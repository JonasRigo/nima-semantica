"""Budget/cancellation checks need no provider credentials or model downloads."""
import asyncio
import threading
import time

import pytest
from test_source_pipeline import store

from nima_semantica.installation import ModelProfile
from nima_semantica.model_runtime import (
    ModelContextExceeded, ModelRequestTimeout, check_context, check_cancelled,
    guarded_model_class, model_diagnostic, run_blocking,
)


def profile(**kwargs):
    return ModelProfile(provider='ollama', model='fixture', max_tokens=128, context_window=4096, **kwargs)


def test_local_defaults_and_conflicting_context():
    assert ModelProfile(provider='ollama', model='fixture').context_window == 32768
    assert ModelProfile(provider='compatible', model='fixture', base_url='http://localhost:1234').context_window is None
    assert ModelProfile(provider='ollama', model='fixture', max_tokens=128, parameters={'num_ctx':4096}).context_window == 4096
    with pytest.raises(ValueError, match='num_ctx must match'):
        profile(parameters={'num_ctx':8192})
    with pytest.raises(ValueError, match='leave room'):
        ModelProfile(provider='ollama',model='fixture',context_window=1024)


def test_context_includes_schema_unicode_and_output_reserve():
    check_context(profile(), [{'role':'user','content':'short'}], {})
    for value, options in [('α'*3000, {}), ('short', {'tools':[{'description':'x'*4000}]}),
                            ('short', {'max_tokens':4000})]:
        with pytest.raises(ModelContextExceeded) as error:
            check_context(profile(), value, options)
        assert error.value.diagnostic['code'] == 'model.context_budget_exceeded'
        assert 'α' not in str(error.value) and 'short' not in str(error.value)


def test_sdk_guard_preserves_results_and_fails_before_network():
    calls=[]
    class SDK:
        def invoke(self, value, **kwargs):
            calls.append(value)
            return {'result':'original'}
    model=guarded_model_class(SDK,profile())()
    assert model.invoke('short') == {'result':'original'}
    with pytest.raises(ModelContextExceeded):model.invoke('x'*4000)
    assert calls == ['short']


def test_timeout_diagnostics_never_expose_provider_message():
    class SDK:
        def invoke(self, *args, **kwargs):raise TimeoutError('secret request body')
    with pytest.raises(ModelRequestTimeout) as exc:
        guarded_model_class(SDK,profile())().invoke('short')
    assert 'secret' not in str(exc.value)
    assert model_diagnostic(exc.value)['timeout_seconds'] == 180
    assert model_diagnostic(ValueError('secret')) is None


@pytest.mark.asyncio
async def test_sync_work_does_not_block_loop_and_keeps_store_thread():
    loop_thread=threading.get_ident()
    seen=[]
    def work():
        seen.append(threading.get_ident())
        time.sleep(.12)
        seen.append(threading.get_ident())
        return 'done'
    task=asyncio.create_task(run_blocking(work))
    await asyncio.sleep(.02)
    assert not task.done()
    assert await task == 'done'
    assert len(set(seen)) == 1 and seen[0] != loop_thread


@pytest.mark.asyncio
async def test_cancellation_waits_for_cleanup_and_stops_next_model_call():
    started=threading.Event()
    cleaned=threading.Event()
    continued=[]
    def work():
        try:
            started.set()
            time.sleep(.12)
            check_cancelled()
            continued.append('must not run')
        finally:
            cleaned.set()
    task=asyncio.create_task(run_blocking(work))
    while not started.is_set():await asyncio.sleep(.005)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):await task
    assert cleaned.is_set() and not continued


def test_budget_failure_has_actionable_controller_feedback(store):
    from test_deep_extraction import context, region, request
    from nima_semantica.deep_extraction_tool import deep_extraction
    r=region(store)
    def model(prompt):raise ModelContextExceeded(5000,128,4096)
    result=deep_extraction(store,request(r),context(),model=model)
    assert result.status == 'failed'
    assert result.data['model_diagnostic']['context_window'] == 4096
    assert 'no model call' in result.data['model_diagnostic']['message'].lower()


@pytest.mark.asyncio
async def test_blocking_canvas_keeps_one_execution_and_responsive_loop():
    pytest.importorskip('lfx')
    from nima_semantica.orchestration.langflow.stages.base import BlockingStage
    calls=[]
    class Slow(BlockingStage):
        name='ReleaseSlowFixture'
        inputs=[]
        def run_sync(self):
            calls.append(threading.get_ident())
            time.sleep(.15)
            return {'data': {'value': 4}}
    stage=Slow()
    first=asyncio.create_task(stage.result_data())
    second=asyncio.create_task(stage.result_data())
    await asyncio.sleep(.03)
    assert not first.done()
    a,b=await asyncio.gather(first,second)
    assert a.data == b.data == {'data': {'value': 4}}
    assert len(calls) == 1 and calls[0] != threading.get_ident()
