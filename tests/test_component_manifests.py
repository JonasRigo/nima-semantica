import ast
from pathlib import Path

from nima_semantica.component_contracts import (
    ComponentAuthority,
    SideEffectClass,
    manifest_for_component,
)


ROOT = Path(__file__).resolve().parents[1]


def wrapper_names():
    return sorted(
        path.stem
        for path in (ROOT / "deploy/langflow_components").rglob("*.py")
        if path.name != "__init__.py"
    )


def test_every_shipped_wrapper_declares_a_manifest():
    wrappers = list(
        (ROOT / "deploy/langflow_components").rglob("*.py")
    )
    assert wrappers
    assert not {"DevelopProof", "ConductDeepResearch"} & set(wrapper_names())
    for name in wrapper_names():
        manifest = manifest_for_component(name)
        assert manifest.component_id
        assert manifest.receipt.mode.value in {"none", "propagated", "emitted"}
    for path in wrappers:
        tree = ast.parse(path.read_text())
        classes = [node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == path.stem]
        assert len(classes) == 1, path
        # Inspectable adapters embed their implementation instead of hiding it
        # behind a subclass-only palette wrapper.
        if any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in {"run", "run_sync", "run_request", "build_tool", "build_backend", "build_embeddings", "build_model"} for node in classes[0].body):
            assert any(isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "nima_manifest" for target in node.targets
            ) for node in classes[0].body), path
            continue
        implementation = next(
            alias
            for node in tree.body
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
            if alias.asname in {"BaseComponent", "ImplementationComponent"}
        )
        module = next(
            node.module
            for node in tree.body
            if isinstance(node, ast.ImportFrom)
            and any(alias is implementation for alias in node.names)
        )
        candidates = list((ROOT / "src").rglob("*.py"))
        implementation_tree = None
        for candidate in candidates:
            candidate_tree = ast.parse(candidate.read_text())
            if any(
                isinstance(node, ast.ClassDef) and node.name == implementation.name
                for node in candidate_tree.body
            ) and (".".join(candidate.with_suffix("").parts[-len(module.split(".")):]) == module):
                implementation_tree = candidate_tree
                break
        if implementation_tree is None:
            implementation_tree = next(
                candidate_tree
                for candidate in candidates
                for candidate_tree in [ast.parse(candidate.read_text())]
                if any(
                    isinstance(node, ast.ClassDef) and node.name == implementation.name
                    for node in candidate_tree.body
                )
            )
        target = next(
            node for node in implementation_tree.body
            if isinstance(node, ast.ClassDef) and node.name == implementation.name
        )
        assert any(
            isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "nima_manifest" for target in node.targets)
            for node in target.body
        ), path


def test_manifest_registry_rejects_retired_tools():
    import pytest
    with pytest.raises(ValueError):
        manifest_for_component("RetrieveVectors")

def test_manifest_ids_preserve_acronyms():
    assert manifest_for_component("ValidateOKFSnapshot").component_id == "validate_okf_snapshot"


def test_maintained_wrappers_import_and_match_declared_manifest():
    import importlib.util
    import sys
    import pytest
    pytest.importorskip("lfx")
    for path in (ROOT / "deploy/langflow_components").rglob("*.py"):
        spec = importlib.util.spec_from_file_location("test_wrapper_" + path.stem, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        classes = [value for value in vars(module).values()
                   if isinstance(value, type) and value.__module__ == module.__name__ and value.__name__ == path.stem]
        assert len(classes) == 1
        assert classes[0].nima_manifest == manifest_for_component(path.stem), (path.stem, classes[0].nima_manifest.model_dump(), manifest_for_component(path.stem).model_dump())


def test_hypothesis_comparison_adapter_uses_contract_not_component_class():
    import asyncio
    import pytest
    pytest.importorskip("lfx")
    from nima_semantica.orchestration.langflow.stages.foundational import HypothesisComparison
    from nima_semantica.workflow_contracts import HypothesisComparisonRequest
    from test_workflow_services import packet, proposal
    context = packet()
    request = HypothesisComparisonRequest(request_id="comparison", corpus_id="papers", project_id="project-a",
                                         graph_revision=context.graph_revision, context_packet=context, hypotheses=(proposal(),))
    component = HypothesisComparison().set(payload={"request": request.model_dump(mode="json"),
        "comparisons": [{"proposal_id": "hypothesis-1", "assessment": "unresolved", "rationale": "Insufficient evidence"}]})
    assert asyncio.run(component.run())["status"] == "complete"
