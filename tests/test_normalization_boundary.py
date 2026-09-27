from nima_semantica.normalization_boundary import execute_normalizer
from nima_semantica.normalization_contracts import (
    FlowAdapterManifest,
    NormalizationStatus,
    NormalizedRequest,
)


def manifest():
    return FlowAdapterManifest(
        adapter_id="example-normalizer",
        name="Example normalizer",
        version="1.0.0",
    )


def request():
    return NormalizedRequest(
        request_id="request-1",
        adapter_id="example-normalizer",
        corpus_id="papers",
        project_id="project-a",
        objective="Find a better lower bound.",
    )


def test_boundary_uses_json_payload_and_validates_output():
    received = {}

    def adapter(payload):
        received.update(payload)
        return {
            "request_id": payload["request_id"],
            "adapter_id": payload["adapter_id"],
            "corpus_id": payload["corpus_id"],
            "project_id": payload["project_id"],
            "status": "complete",
            "metadata": {"flow": "langflow"},
        }

    output = execute_normalizer(manifest(), request(), adapter)

    assert output.status == NormalizationStatus.COMPLETE
    assert received["objective"] == "Find a better lower bound."
    assert output.metadata["flow"] == "langflow"


def test_boundary_converts_adapter_exception_to_failed_output():
    def adapter(_payload):
        raise RuntimeError("simulated flow failure")

    output = execute_normalizer(manifest(), request(), adapter)

    assert output.status == NormalizationStatus.FAILED
    assert output.authority == "proposal_only"
    assert output.diagnostics[0].code == "normalizer.execution_error"
    assert "simulated flow failure" not in output.diagnostics[0].message


def test_boundary_rejects_output_with_different_graph_revision():
    from nima_semantica.okf_contracts import GraphRevision
    revision = GraphRevision(corpus_id="papers", project_id="project-a", project_revision=4)
    scoped_request = request().model_copy(update={"graph_revision": revision})

    def adapter(payload):
        return {
            "request_id": payload["request_id"],
            "adapter_id": payload["adapter_id"],
            "corpus_id": payload["corpus_id"],
            "project_id": payload["project_id"],
            "graph_revision": revision.model_copy(update={"project_revision": 3}).model_dump(mode="json"),
            "status": "complete",
        }

    output = execute_normalizer(manifest(), scoped_request, adapter)

    assert output.status == NormalizationStatus.FAILED
    assert output.graph_revision == revision
    assert output.diagnostics[0].code == "normalizer.execution_error"
