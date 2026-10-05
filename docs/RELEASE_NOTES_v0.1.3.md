# NIMA-Semantica v0.1.3

This candidate repairs extraction diagnostics and corpus inspection memory use, and provides reusable helpers for durable sequential extraction recovery.
It retains source evidence, operation identities and proposal-only scientific authority.

## Extraction and recovery

Every proposed node and edge must supply its own supporting exact citation.
Schema rejections now identify all grounding errors, explain how to repair them using returned citation handles, and preserve the last rejection and rejection count in terminal outcomes.
Controller feedback identifies the next proposal, analysis or submission stage.
The tool still rejects invented evidence, invalid quotations and ontology violations.

The pinned mathematical ontology preserves explicit statement fields, conventions, unresolved assumptions and proof-specific prerequisites during extraction and consolidation.
Source-region iteration and bounded retrieval retain source fidelity limitations and avoid accumulating repeated large book diagnostics.
These changes carried forward from the patched v0.1.2 candidate are included in the v0.1.3 source inventory.

The `nima_semantica.extraction_recovery` helpers distinguish returned failures from unknown delivery, select only failed regions that remain pending, and validate a fresh plan without expanding its original region scope.
A retry needs a new `plan_attempt_id`; the saved failed operation remains immutable.
See the [extraction recovery recipe](#extraction-recovery-recipe) below.

## Corpus inspection

Inspect Corpus now uses the store's scoped iterator rather than materializing source-region payloads in a list.
It decodes one record at a time while retaining the coherent snapshot, scope checks, source binding validation, readiness semantics and revision-bound pagination.
Memory still grows with source descriptors and region identifiers, but repeated diagnostic payloads do not accumulate across the corpus.

## Extraction recovery recipe

Persist each planned request, regional request and returned result before moving to the next batch.
After a returned failure, compute the pending region set from accepted coverage receipts and select only its intersection with failed saved batches.
An absent result requires reconciliation using the original operation ID and identical request before a new attempt can be planned.

```python
from nima_semantica.extraction_recovery import failed_plan_regions, recovery_plan_request

failed = failed_plan_regions(saved_plan, result_directory, pending_regions)
if failed:
    retry = recovery_plan_request(original_plan_request, failed, attempt_id="reviewed-recovery-1")
    # Submit retry to Deep Extraction plan mode, then submit its returned batches unchanged.
```

These helpers neither run a scheduler nor automatically retry operations.
Keep sequential storage access and stop for diagnosis if the reviewed recovery fails.
Consolidation and exact graph admission remain separate steps with their existing authorization requirements.

## Upgrade and qualification

See [candidate qualification](PLATFORM_SUPPORT_v0.1.3.md) for fresh checks and remaining gates.
Install the matching wheel, run `nima doctor`, and refresh managed images and project toolboxes through the documented setup workflow.
Versioned Langflow images use `nima-langflow:0.1.3`.
Installing a wheel does not replace an already running worker.
Preserve the existing corpus and provider configuration during upgrade.
The complete research extraction campaign is still in progress; its partial coverage is not full-corpus scientific verification.
