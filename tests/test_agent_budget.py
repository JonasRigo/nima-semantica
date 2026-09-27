import asyncio

import pytest

from nima_semantica.agent_budget import AgentBudget, AgentLimits, BudgetExceeded, Capability
from nima_semantica.okf_contracts import GraphRevision


def budget(**kwargs):
    return AgentBudget(run_id="r", revision=GraphRevision(corpus_id="papers"), **kwargs)


def test_nested_attempts_share_limits_and_preserve_failures():
    run = budget(limits=AgentLimits(model_calls=2, tokens=10, max_depth=2))
    parent = run.begin("coordinator", {}, kind="model", reserve_tokens=6)
    child = run.begin("worker", {}, kind="model", parent_id=parent, reserve_tokens=4)
    with pytest.raises(BudgetExceeded):
        run.begin("third", {}, kind="model", reserve_tokens=1)
    with pytest.raises(BudgetExceeded):
        run.begin("nested", {}, parent_id=child)
    run.finish(child, state="failed", error="invalid_output")
    run.finish(parent, result={"status": "unresolved"}, tokens=3)
    assert [item.state for item in run.attempts] == ["completed", "failed"]
    assert not run.attempts[1].usage_reported
    assert run.attempts[1].accounted_tokens == 4
    with pytest.raises(ValueError, match="running parent"):
        run.begin("late", {}, parent_id=parent)


def test_unknown_usage_and_provider_overrun_fail_closed():
    run = budget(limits=AgentLimits(tokens=10))
    first = run.begin("model", {}, kind="model", reserve_tokens=10)
    run.finish(first)
    with pytest.raises(BudgetExceeded):
        run.begin("model", {}, kind="model", reserve_tokens=1)
    run = budget()
    first = run.begin("model", {}, kind="model", reserve_tokens=5)
    with pytest.raises(BudgetExceeded):
        run.finish(first, result="retained", tokens=6)
    assert run.attempts[0].result == "retained"
    with pytest.raises(BudgetExceeded):
        run.begin("reader", {})


def test_deadline_and_unauthorized_capabilities():
    ticks = [0]
    run = budget(clock=lambda: ticks[0], limits=AgentLimits(seconds=1))
    ticks[0] = 2
    with pytest.raises(BudgetExceeded):
        run.begin("read", {})
    with pytest.raises(ValueError, match="not authorized"):
        asyncio.run(budget().call("commit", {}, capabilities=()))


def test_failed_tool_call_is_retained():
    async def fail(payload):
        raise RuntimeError("sensitive provider exception")
    run = budget()
    with pytest.raises(RuntimeError):
        asyncio.run(run.call("read", {}, capabilities=(Capability("read", fail),)))
    assert run.attempts[0].state == "failed"
    assert run.attempts[0].error == "RuntimeError"


def test_capability_scope_cannot_expand():
    async def forbidden(payload):
        pytest.fail("foreign scope reached capability")
    with pytest.raises(ValueError, match="scope or revision"):
        asyncio.run(budget().call("read", {"corpus_id": "foreign"},
                    capabilities=(Capability("read", forbidden),)))
