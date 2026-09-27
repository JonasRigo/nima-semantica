"""Supplementary injected-fault checks; these do not execute Lean."""
import pytest
from nima_semantica.execution_receipts import ExecutionReceiptService
from nima_semantica.verify_lean_tool import verify_lean
from test_verify_lean_tool import Verifier, request, context
from test_source_pipeline import store


def test_outer_receipt_failure_rolls_back_outer_publication(store,monkeypatch):
    original=ExecutionReceiptService.record
    def fail_outer(self,receipt):
        if receipt.stage=='verify_lean':raise OSError('injected storage failure')
        return original(self,receipt)
    monkeypatch.setattr(ExecutionReceiptService,'record',fail_outer)
    with pytest.raises(OSError):verify_lean(store,request(),context(),verifier=Verifier())
    assert not store.records('LeanVerificationOutcome')
    assert not store.records('LeanVerificationProgressProposal')
    receipts=[r.content for _,r in store.records('ExecutionReceipt')]
    assert not any(r['stage']=='verify_lean' for r in receipts)
    assert any(r['stage']=='lean_verification' for r in receipts)
    assert store.records('LeanProofAttempt')


def test_environment_changed_during_execution_is_not_accepted(store):
    class Changed(Verifier):
        def verify(self,store,request):
            result=super().verify(store,request)
            self.timeout+=1
            return result
    result=verify_lean(store,request(),context(),verifier=Changed())
    assert not result.data['formal_verification_accepted']
    assert result.status=='failed'


def test_missing_operator_environment_records_failure(store,monkeypatch):
    def missing():raise ValueError('not configured')
    monkeypatch.setattr('nima_semantica.lean_transport.configured_lean_verifier',missing)
    result=verify_lean(store,request(),context())
    assert result.status=='failed' and not result.data['executed']
    assert not result.data['formal_verification_accepted']
    assert store.records('LeanVerificationOutcome')
