"""Framework-independent declarations for NIMA component boundaries.

These declarations describe what a component is allowed to receive, return,
and change. They are metadata contracts, not an execution engine and do not
import Langflow or any persistence implementation.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from .okf_contracts import GraphRevision
from .models import StrictModel
from .okf_contracts import GraphIdentifier
from .registry_contracts import RegistryResourceKind


class ComponentAuthority(StrEnum):
    TRANSFORMATION = "transformation"
    READ_ONLY = "read_only"
    PROPOSAL_ONLY = "proposal_only"
    ARTIFACT_WRITE = "artifact_write"
    APPROVED_COMMIT = "approved_commit"


class SideEffectClass(StrEnum):
    PURE_TRANSFORM = "pure_transform"
    READ_ONLY_RETRIEVAL = "read_only_retrieval"
    PROPOSAL_ONLY = "proposal_only"
    ARTIFACT_WRITE = "artifact_write"
    GRAPH_COMMIT = "graph_commit"


class ComponentCapability(StrEnum):
    MODEL_CALL = "model_call"


class ReceiptMode(StrEnum):
    NONE = "none"
    PROPAGATED = "propagated"
    EMITTED = "emitted"


class ReceiptDeclaration(StrictModel):
    """Explicit receipt behavior at a component boundary."""

    mode: ReceiptMode
    contract_id: GraphIdentifier | None = None
    field: GraphIdentifier = "receipt_ids"

    @model_validator(mode="after")
    def require_contract_for_receipts(self) -> "ReceiptDeclaration":
        if self.mode is not ReceiptMode.NONE and self.contract_id is None:
            raise ValueError("receipt-producing components require a receipt contract")
        if self.mode is ReceiptMode.NONE and self.contract_id is not None:
            raise ValueError("receipt contract is invalid when receipt mode is none")
        return self


ScopeField = Literal["corpus_id", "project_id"]
RevisionField = Literal["registry_revision", "graph_revision", "source_revision", "projection_revision"]


class ComponentManifest(StrictModel):
    """Machine-readable contract for a NIMA or imported-flow adapter."""

    schema_version: Literal[1] = 1
    component_id: GraphIdentifier
    version: str = Field(min_length=1, max_length=128)
    input_contract: GraphIdentifier
    output_contract: GraphIdentifier
    scope_fields: tuple[ScopeField, ...] = ("corpus_id", "project_id")
    revision_fields: tuple[RevisionField, ...] = ("graph_revision",)
    registry_resource_kinds: tuple[RegistryResourceKind, ...] = ()
    diagnostics: Literal["required"] = "required"
    authority: ComponentAuthority
    side_effect_class: SideEffectClass
    capabilities: tuple[ComponentCapability, ...] = ()
    receipt: ReceiptDeclaration

    @model_validator(mode="after")
    def validate_declaration(self) -> "ComponentManifest":
        if len(self.scope_fields) != len(set(self.scope_fields)):
            raise ValueError("component scope fields must be unique")
        if len(self.revision_fields) != len(set(self.revision_fields)):
            raise ValueError("component revision fields must be unique")
        if self.registry_resource_kinds and "registry_revision" not in self.revision_fields:
            raise ValueError("registry-bound components must declare registry_revision")
        if self.side_effect_class is SideEffectClass.GRAPH_COMMIT and self.authority is not ComponentAuthority.APPROVED_COMMIT:
            raise ValueError("graph_commit requires approved_commit authority")
        if self.authority is ComponentAuthority.APPROVED_COMMIT and self.side_effect_class is not SideEffectClass.GRAPH_COMMIT:
            raise ValueError("approved_commit authority requires graph_commit=True")
        if self.authority is ComponentAuthority.TRANSFORMATION and self.side_effect_class is not SideEffectClass.PURE_TRANSFORM:
            raise ValueError("transformation authority cannot persist, retrieve, or commit")
        if self.authority is ComponentAuthority.READ_ONLY and self.side_effect_class is not SideEffectClass.READ_ONLY_RETRIEVAL:
            raise ValueError("read_only authority requires read_only_retrieval")
        if self.side_effect_class is SideEffectClass.ARTIFACT_WRITE and self.authority is not ComponentAuthority.ARTIFACT_WRITE:
            raise ValueError("artifact_write requires artifact_write authority")
        if self.side_effect_class is SideEffectClass.PROPOSAL_ONLY and self.authority is not ComponentAuthority.PROPOSAL_ONLY:
            raise ValueError("proposal_only requires proposal_only authority")
        if self.side_effect_class is SideEffectClass.GRAPH_COMMIT and self.authority not in (
            ComponentAuthority.ARTIFACT_WRITE,
            ComponentAuthority.APPROVED_COMMIT,
        ):
            raise ValueError("graph_commit requires approved_commit authority")
        return self


class ComponentExecutionContext(StrictModel):
    """Scope and revision context propagated through a component boundary."""

    corpus_id: GraphIdentifier
    project_id: GraphIdentifier | None = None
    graph_revision: GraphRevision | None = None
    source_revision: GraphIdentifier | None = None
    projection_revision: GraphIdentifier | None = None
    registry_revision: GraphIdentifier | None = None


FOUNDATIONAL_COMPONENT_NAMES = (
    "InputNormalizer",
    "GraphRetrievalNormalizer",
    "HypothesisGeneration",
    "HypothesisComparison",
    "OutputNormalizer",
    "GraphCommit",
)


def _foundational_manifest(name: str) -> ComponentManifest | None:
    """Return the frozen manifest for a foundational flow boundary."""

    common = {
        "version": "2",
        "scope_fields": ("corpus_id", "project_id"),
        "revision_fields": ("registry_revision", "graph_revision", "source_revision"),
        "diagnostics": "required",
    }
    declarations = {
        "InputNormalizer": {
            "version": "3",
            "revision_fields": (),
            "component_id": "input_normalizer",
            "input_contract": "native_flow_input",
            "output_contract": "adapter_context",
            "authority": ComponentAuthority.READ_ONLY,
            "side_effect_class": SideEffectClass.READ_ONLY_RETRIEVAL,
            "registry_resource_kinds": (),
            "receipt": ReceiptDeclaration(mode=ReceiptMode.NONE),
        },
        "GraphRetrievalNormalizer": {
            "version": "3",
            "revision_fields": ("graph_revision",),
            "component_id": "graph_retrieval_normalizer",
            "input_contract": "adapter_context_query",
            "output_contract": "graph_retrieval_normalizer_output",
            "authority": ComponentAuthority.READ_ONLY,
            "side_effect_class": SideEffectClass.READ_ONLY_RETRIEVAL,
            "registry_resource_kinds": (),
            "receipt": ReceiptDeclaration(mode=ReceiptMode.EMITTED, contract_id="operation_receipt"),
        },
        "HypothesisGeneration": {
            "component_id": "hypothesis_generation",
            "input_contract": "hypothesis_generation_request",
            "output_contract": "hypothesis_generation_output",
            "authority": ComponentAuthority.PROPOSAL_ONLY,
            "side_effect_class": SideEffectClass.PROPOSAL_ONLY,
            "registry_resource_kinds": (RegistryResourceKind.GRAPH_SNAPSHOT, RegistryResourceKind.ARTIFACT),
            "receipt": ReceiptDeclaration(mode=ReceiptMode.PROPAGATED, contract_id="operation_receipt"),
        },
        "HypothesisComparison": {
            "component_id": "hypothesis_comparison",
            "input_contract": "hypothesis_comparison_request",
            "output_contract": "hypothesis_comparison_output",
            "authority": ComponentAuthority.PROPOSAL_ONLY,
            "side_effect_class": SideEffectClass.PROPOSAL_ONLY,
            "registry_resource_kinds": (RegistryResourceKind.GRAPH_SNAPSHOT, RegistryResourceKind.PROJECTION, RegistryResourceKind.ARTIFACT),
            "receipt": ReceiptDeclaration(mode=ReceiptMode.PROPAGATED, contract_id="operation_receipt"),
        },
        "OutputNormalizer": {
            "version": "3",
            "revision_fields": (),
            "component_id": "output_normalizer",
            "input_contract": "user_flow_output",
            "output_contract": "adapted_output",
            "authority": ComponentAuthority.PROPOSAL_ONLY,
            "side_effect_class": SideEffectClass.PROPOSAL_ONLY,
            "registry_resource_kinds": (),
            "receipt": ReceiptDeclaration(mode=ReceiptMode.NONE),
        },
        "GraphCommit": {
            "component_id": "graph_commit",
            "input_contract": "graph_commit_request",
            "output_contract": "graph_commit_output",
            "authority": ComponentAuthority.APPROVED_COMMIT,
            "side_effect_class": SideEffectClass.GRAPH_COMMIT,
            "registry_resource_kinds": (RegistryResourceKind.GRAPH_DELTA, RegistryResourceKind.GRAPH_SNAPSHOT),
            "receipt": ReceiptDeclaration(mode=ReceiptMode.EMITTED, contract_id="operation_receipt"),
        },
    }
    declaration = declarations.get(name)
    return ComponentManifest(**(common | declaration)) if declaration else None


def validate_foundational_manifests() -> tuple[ComponentManifest, ...]:
    """Validate the frozen foundational boundary declarations."""

    manifests = tuple(_foundational_manifest(name) for name in FOUNDATIONAL_COMPONENT_NAMES)
    if any(manifest is None for manifest in manifests):
        raise ValueError("foundational component manifest registry is incomplete")
    result = tuple(manifest for manifest in manifests if manifest is not None)
    committers = [item for item in result if item.side_effect_class is SideEffectClass.GRAPH_COMMIT]
    if len(committers) != 1 or committers[0].component_id != "graph_commit":
        raise ValueError("exactly the GraphCommit adapter may declare graph_commit")
    if any(
        item.side_effect_class is SideEffectClass.PROPOSAL_ONLY
        and item.authority is not ComponentAuthority.PROPOSAL_ONLY
        for item in result
    ):
        raise ValueError("proposal boundaries must declare proposal_only authority")
    return result


def validate_manifest_execution(
    manifest: ComponentManifest,
    context: ComponentExecutionContext,
) -> None:
    """Ensure a declared component receives every scope/revision it requires."""

    values = context.model_dump()
    missing = [
        field
        for field in (*manifest.scope_fields, *manifest.revision_fields)
        if values.get(field) is None and field != "project_id"
    ]
    if missing:
        raise ValueError(f"component context is missing declared fields: {', '.join(missing)}")


def manifest_for_component(name: str) -> ComponentManifest:
    """Resolve a maintained foundational adapter, rejecting retired tools."""
    if name == "ConfiguredModel":
        return ComponentManifest(component_id="configured_model", version="1",
            input_contract="model_profile", output_contract="language_model",
            scope_fields=(), revision_fields=(), capabilities=(ComponentCapability.MODEL_CALL,),
            authority=ComponentAuthority.READ_ONLY, side_effect_class=SideEffectClass.READ_ONLY_RETRIEVAL,
            receipt=ReceiptDeclaration(mode=ReceiptMode.NONE))
    if name == "ConfiguredEmbeddings":
        return ComponentManifest(component_id="configured_embeddings", version="1",
            input_contract="embedding_profile", output_contract="embeddings",
            scope_fields=(), revision_fields=(), capabilities=(ComponentCapability.MODEL_CALL,),
            authority=ComponentAuthority.READ_ONLY, side_effect_class=SideEffectClass.READ_ONLY_RETRIEVAL,
            receipt=ReceiptDeclaration(mode=ReceiptMode.NONE))
    if name.startswith("MathMCP"):
        from .math_mcp_contracts import TOOLS
        operations = {"MathMCP" + "".join(part.title() for part in op.split("_")): op for op in (*TOOLS, "operations")}
        if name not in operations:
            raise ValueError(f"Unknown maintained component: {name}")
        operation = operations[name]
        read_only = operation in {"operations", "status", "frontier", "inspect", "export"}
        return ComponentManifest(
            component_id="math_mcp_" + operation, version="1",
            input_contract="math_mcp_" + operation + "_request", output_contract="math_mcp_result",
            scope_fields=(), revision_fields=(), capabilities=(),
            authority=ComponentAuthority.READ_ONLY if read_only else ComponentAuthority.ARTIFACT_WRITE,
            side_effect_class=SideEffectClass.READ_ONLY_RETRIEVAL if read_only else SideEffectClass.ARTIFACT_WRITE,
            receipt=ReceiptDeclaration(mode=ReceiptMode.NONE),
        )
    if name in ("ProjectUpdateFields","UpdateProjectGraph","ProjectUpdatePolicy","ProjectUpdateView"):
        execute=name=="UpdateProjectGraph"
        cid,inputs,outputs={"ProjectUpdateFields":("project_update_fields","native_flow_input","update_project_request"),
            "UpdateProjectGraph":("update_project_graph","update_project_request","tool_result"),
            "ProjectUpdatePolicy":("project_update_policy","none","project_update_policy"),
            "ProjectUpdateView":("project_update_view","tool_result","project_update_result")}[name]
        return ComponentManifest(component_id=cid,version="1",input_contract=inputs,output_contract=outputs,
            scope_fields=("corpus_id","project_id") if execute else (),revision_fields=("graph_revision",) if execute else (),capabilities=(),
            authority=ComponentAuthority.APPROVED_COMMIT if execute else ComponentAuthority.TRANSFORMATION,
            side_effect_class=SideEffectClass.GRAPH_COMMIT if execute else SideEffectClass.PURE_TRANSFORM,
            receipt=ReceiptDeclaration(mode=ReceiptMode.EMITTED,contract_id="execution_receipt") if execute else ReceiptDeclaration(mode=ReceiptMode.NONE))
    if name in ("DependencyTraceFields","TraceClaimDependencies","DependencyTracePolicy","DependencyTraceView"):
        execute=name=="TraceClaimDependencies"
        cid,inputs,outputs={"DependencyTraceFields":("dependency_trace_fields","native_flow_input","trace_dependencies_request"),
            "TraceClaimDependencies":("trace_claim_dependencies","trace_dependencies_request","tool_result"),
            "DependencyTracePolicy":("dependency_trace_policy","none","dependency_trace_policy"),
            "DependencyTraceView":("dependency_trace_view","tool_result","dependency_trace_findings")}[name]
        return ComponentManifest(component_id=cid,version="1",input_contract=inputs,output_contract=outputs,
            scope_fields=("corpus_id","project_id") if execute else (),revision_fields=(),capabilities=(),
            authority=ComponentAuthority.ARTIFACT_WRITE if execute else ComponentAuthority.TRANSFORMATION,
            side_effect_class=SideEffectClass.ARTIFACT_WRITE if execute else SideEffectClass.PURE_TRANSFORM,
            receipt=ReceiptDeclaration(mode=ReceiptMode.EMITTED,contract_id="execution_receipt") if execute else ReceiptDeclaration(mode=ReceiptMode.NONE))
    if name in ("GraphAnalysisFields","AnalyzeGraph","GraphAnalysisPolicy","GraphAnalysisView"):
        execute=name=="AnalyzeGraph"
        cid,inputs,outputs={"GraphAnalysisFields":("graph_analysis_fields","native_flow_input","analyze_graph_request"),
            "AnalyzeGraph":("analyze_graph","analyze_graph_request","tool_result"),
            "GraphAnalysisPolicy":("graph_analysis_policy","none","graph_analysis_policy"),
            "GraphAnalysisView":("graph_analysis_view","tool_result","graph_analysis_findings")}[name]
        return ComponentManifest(component_id=cid,version="1",input_contract=inputs,output_contract=outputs,
            scope_fields=("corpus_id","project_id") if execute else (),revision_fields=(),capabilities=(),
            authority=ComponentAuthority.ARTIFACT_WRITE if execute else ComponentAuthority.TRANSFORMATION,
            side_effect_class=SideEffectClass.ARTIFACT_WRITE if execute else SideEffectClass.PURE_TRANSFORM,
            receipt=ReceiptDeclaration(mode=ReceiptMode.EMITTED,contract_id="execution_receipt") if execute else ReceiptDeclaration(mode=ReceiptMode.NONE))
    if name in ("ReviewFields","ReviewResearch","ReviewOntology","ReviewRetrieval","ReviewStateView"):
        execute=name=="ReviewResearch"
        cid,inputs,outputs={"ReviewFields":("review_fields","native_flow_input","review_request"),
            "ReviewResearch":("review_research","review_request","tool_result"),
            "ReviewOntology":("review_ontology","none","private_review_policy"),
            "ReviewRetrieval":("review_retrieval","none","math_retrieval_policy"),
            "ReviewStateView":("review_state_view","tool_result","private_review_state")}[name]
        return ComponentManifest(component_id=cid,version="1",input_contract=inputs,output_contract=outputs,
            scope_fields=("corpus_id","project_id") if execute else (),revision_fields=(),
            authority=ComponentAuthority.ARTIFACT_WRITE if execute else ComponentAuthority.TRANSFORMATION,
            side_effect_class=SideEffectClass.ARTIFACT_WRITE if execute else SideEffectClass.PURE_TRANSFORM,
            capabilities=(ComponentCapability.MODEL_CALL,) if execute else (),
            receipt=ReceiptDeclaration(mode=ReceiptMode.EMITTED,contract_id="execution_receipt") if execute else ReceiptDeclaration(mode=ReceiptMode.NONE))

    if name in ("DraftLeanFields","DraftLean","LeanDraftOntology","LeanDraftRetrieval","LeanDraftStateView","LeanSearch","LeanDraftVerification"):
        execute=name=="DraftLean"
        cid,inputs,outputs={"DraftLeanFields":("draft_lean_fields","native_flow_input","draft_lean_request"),
            "DraftLean":("draft_lean","draft_lean_request","tool_result"),
            "LeanDraftOntology":("lean_draft_ontology","none","private_formalization_policy"),
            "LeanDraftRetrieval":("lean_draft_retrieval","none","math_retrieval_policy"),
            "LeanDraftStateView":("lean_draft_state_view","tool_result","private_formalization_state"),
            "LeanSearch":("lean_search","none","lean_search_capability"),
            "LeanDraftVerification":("lean_draft_verification","none","lean_verification_capability")}[name]
        return ComponentManifest(component_id=cid,version="2",input_contract=inputs,output_contract=outputs,
            scope_fields=("corpus_id","project_id") if execute else (),revision_fields=(),
            authority=ComponentAuthority.ARTIFACT_WRITE if execute else ComponentAuthority.TRANSFORMATION,
            side_effect_class=SideEffectClass.ARTIFACT_WRITE if execute else SideEffectClass.PURE_TRANSFORM,
            capabilities=(ComponentCapability.MODEL_CALL,) if execute else (),
            receipt=ReceiptDeclaration(mode=ReceiptMode.EMITTED,contract_id="execution_receipt") if execute else ReceiptDeclaration(mode=ReceiptMode.NONE))
    if name == "PaperDiscovery":
        return ComponentManifest(component_id="paper_discovery",version="1",input_contract="paper_discovery_policy",
            output_contract="paper_discovery_tool",scope_fields=(),revision_fields=(),
            authority=ComponentAuthority.READ_ONLY,side_effect_class=SideEffectClass.READ_ONLY_RETRIEVAL,
            capabilities=(),receipt=ReceiptDeclaration(mode=ReceiptMode.NONE))
    if name == "StockArxivHTML":
        return ComponentManifest(component_id="stock_arxiv_html",version="3",input_contract="short_research_query",
            output_contract="arxiv_metadata_html_preview_or_tool",scope_fields=(),revision_fields=(),
            authority=ComponentAuthority.READ_ONLY,side_effect_class=SideEffectClass.READ_ONLY_RETRIEVAL,
            receipt=ReceiptDeclaration(mode=ReceiptMode.NONE))
    if name == "DeepResearchPassFields":
        return ComponentManifest(component_id="deep_research_pass_fields",version="2",input_contract="native_flow_input",
            output_contract="deep_research_pass_request",scope_fields=(),revision_fields=("graph_revision",),
            authority=ComponentAuthority.TRANSFORMATION,side_effect_class=SideEffectClass.PURE_TRANSFORM,
            receipt=ReceiptDeclaration(mode=ReceiptMode.NONE))
    if name == "ConductPassDeepResearch":
        return ComponentManifest(component_id="deep_research_passes",version="4",input_contract="deep_research_pass_request",
            output_contract="deep_research_review_proposal",scope_fields=("corpus_id","project_id"),revision_fields=("graph_revision",),
            authority=ComponentAuthority.ARTIFACT_WRITE,side_effect_class=SideEffectClass.ARTIFACT_WRITE,
            capabilities=(ComponentCapability.MODEL_CALL,),receipt=ReceiptDeclaration(mode=ReceiptMode.EMITTED,contract_id="execution_receipt"))
    if name == "SimpleDeepResearch":
        return ComponentManifest(component_id="deep_research_simple",version="1",input_contract="deep_research_request",
            output_contract="deep_research_review_proposal",scope_fields=("corpus_id","project_id"),revision_fields=("graph_revision",),
            authority=ComponentAuthority.ARTIFACT_WRITE,side_effect_class=SideEffectClass.ARTIFACT_WRITE,
            capabilities=(ComponentCapability.MODEL_CALL,),receipt=ReceiptDeclaration(mode=ReceiptMode.EMITTED,contract_id="execution_receipt"))
    if name == "ReviewGraphCommit":
        return ComponentManifest(component_id="review_graph_commit",version="1",input_contract="deep_research_review_proposal",
            output_contract="project_graph_commit_result",scope_fields=("corpus_id","project_id"),revision_fields=("graph_revision",),
            authority=ComponentAuthority.APPROVED_COMMIT,side_effect_class=SideEffectClass.GRAPH_COMMIT,
            receipt=ReceiptDeclaration(mode=ReceiptMode.EMITTED,contract_id="execution_receipt"))

    if name in ("ComparisonFields","CompareResearchObjects","ComparisonOntology","ComparisonRetrieval","ComparisonStateView"):
        execute=name=="CompareResearchObjects"
        cid,inputs,outputs={"ComparisonFields":("comparison_fields","native_flow_input","compare_research_objects_request"),
            "CompareResearchObjects":("compare_research_objects","compare_research_objects_request","tool_result"),
            "ComparisonOntology":("comparison_ontology","none","private_comparison_policy"),
            "ComparisonRetrieval":("comparison_retrieval","none","math_retrieval_policy"),
            "ComparisonStateView":("comparison_state_view","tool_result","private_comparison_state")}[name]
        return ComponentManifest(component_id=cid,version="1",input_contract=inputs,output_contract=outputs,
            scope_fields=("corpus_id","project_id") if execute else (),revision_fields=(),
            authority=ComponentAuthority.ARTIFACT_WRITE if execute else ComponentAuthority.TRANSFORMATION,
            side_effect_class=SideEffectClass.ARTIFACT_WRITE if execute else SideEffectClass.PURE_TRANSFORM,
            capabilities=(ComponentCapability.MODEL_CALL,) if execute else (),
            receipt=ReceiptDeclaration(mode=ReceiptMode.EMITTED,contract_id="execution_receipt") if execute else ReceiptDeclaration(mode=ReceiptMode.NONE))
    if name in ("HypothesisFields","GenerateHypotheses","HypothesisOntology","HypothesisRetrieval","HypothesisStateView"):
        execute=name=="GenerateHypotheses"
        cid,inputs,outputs={"HypothesisFields":("hypothesis_fields","native_flow_input","generate_hypotheses_request"),
            "GenerateHypotheses":("generate_hypotheses","generate_hypotheses_request","tool_result"),
            "HypothesisOntology":("hypothesis_ontology","none","private_hypothesis_policy"),
            "HypothesisRetrieval":("hypothesis_retrieval","none","math_retrieval_policy"),
            "HypothesisStateView":("hypothesis_state_view","tool_result","private_hypothesis_state")}[name]
        return ComponentManifest(component_id=cid,version="1",input_contract=inputs,output_contract=outputs,
            scope_fields=("corpus_id","project_id") if execute else (),revision_fields=(),
            authority=ComponentAuthority.ARTIFACT_WRITE if execute else ComponentAuthority.TRANSFORMATION,
            side_effect_class=SideEffectClass.ARTIFACT_WRITE if execute else SideEffectClass.PURE_TRANSFORM,
            capabilities=(ComponentCapability.MODEL_CALL,) if execute else (),
            receipt=ReceiptDeclaration(mode=ReceiptMode.EMITTED,contract_id="execution_receipt") if execute else ReceiptDeclaration(mode=ReceiptMode.NONE))
    if name in ("SubstantiationFields","SubstantiateGraphSnapshot","SubstantiationOntology","SubstantiationRetrieval","SubstantiationStateView"):
        execute=name=="SubstantiateGraphSnapshot"
        cid,inputs,outputs={"SubstantiationFields":("substantiation_fields","native_flow_input","substantiate_graph_snapshot_request"),
            "SubstantiateGraphSnapshot":("substantiate_graph_snapshot","substantiate_graph_snapshot_request","tool_result"),
            "SubstantiationOntology":("substantiation_ontology","none","private_substantiation_policy"),
            "SubstantiationRetrieval":("substantiation_retrieval","none","math_retrieval_policy"),
            "SubstantiationStateView":("substantiation_state_view","tool_result","private_substantiation_state")}[name]
        return ComponentManifest(component_id=cid,version="1",input_contract=inputs,output_contract=outputs,
            scope_fields=("corpus_id","project_id") if execute else (),revision_fields=(),
            authority=ComponentAuthority.ARTIFACT_WRITE if execute else ComponentAuthority.TRANSFORMATION,
            side_effect_class=SideEffectClass.ARTIFACT_WRITE if execute else SideEffectClass.PURE_TRANSFORM,
            capabilities=(ComponentCapability.MODEL_CALL,) if execute else (),
            receipt=ReceiptDeclaration(mode=ReceiptMode.EMITTED,contract_id="execution_receipt") if execute else ReceiptDeclaration(mode=ReceiptMode.NONE))
    if name in ("ExtractionFields","DeepExtraction","ExtractionOntology","ExtractionRetrieval","ExtractionStateView"):
        execute=name=="DeepExtraction"
        cid,inputs,outputs={"ExtractionFields":("extraction_fields","native_flow_input","deep_extraction_request"),
            "DeepExtraction":("deep_extraction","deep_extraction_request","tool_result"),
            "ExtractionOntology":("extraction_ontology","none","private_extraction_policy"),
            "ExtractionRetrieval":("extraction_retrieval","none","math_retrieval_policy"),
            "ExtractionStateView":("extraction_state_view","tool_result","private_extraction_state")}[name]
        return ComponentManifest(component_id=cid,version="1",input_contract=inputs,output_contract=outputs,
            scope_fields=("corpus_id","project_id") if execute else (),revision_fields=(),
            authority=ComponentAuthority.ARTIFACT_WRITE if execute else ComponentAuthority.TRANSFORMATION,
            side_effect_class=SideEffectClass.ARTIFACT_WRITE if execute else SideEffectClass.PURE_TRANSFORM,
            capabilities=(ComponentCapability.MODEL_CALL,) if execute else (),
            receipt=ReceiptDeclaration(mode=ReceiptMode.EMITTED,contract_id="execution_receipt") if execute else ReceiptDeclaration(mode=ReceiptMode.NONE))
    if name in ("SaveAnalysisFields","SaveResearchAnalysis","ResearchAnalysisView"):
        execute = name == "SaveResearchAnalysis"
        inputs,outputs={"SaveAnalysisFields":("native_flow_input","save_analysis_request"),
            "SaveResearchAnalysis":("save_analysis_request","tool_result"),"ResearchAnalysisView":("tool_result","research_analysis_artifacts")}[name]
        return ComponentManifest(component_id={"SaveAnalysisFields":"save_analysis_fields","SaveResearchAnalysis":"save_research_analysis",
            "ResearchAnalysisView":"research_analysis_view"}[name],version="1",input_contract=inputs,output_contract=outputs,
            scope_fields=("corpus_id","project_id") if execute else (),revision_fields=(),capabilities=(),
            authority=ComponentAuthority.ARTIFACT_WRITE if execute else ComponentAuthority.TRANSFORMATION,
            side_effect_class=SideEffectClass.ARTIFACT_WRITE if execute else SideEffectClass.PURE_TRANSFORM,
            receipt=ReceiptDeclaration(mode=ReceiptMode.EMITTED,contract_id="execution_receipt") if execute else ReceiptDeclaration(mode=ReceiptMode.NONE))
    if name in ("VerifyLeanFields","VerifyLean","LeanVerificationView"):
        execute = name == "VerifyLean"
        inputs,outputs={"VerifyLeanFields":("native_flow_input","verify_lean_request"),
            "VerifyLean":("verify_lean_request","tool_result"),"LeanVerificationView":("tool_result","lean_verification_evidence")}[name]
        return ComponentManifest(component_id={"VerifyLeanFields":"verify_lean_fields","VerifyLean":"verify_lean",
            "LeanVerificationView":"lean_verification_view"}[name],version="2",input_contract=inputs,output_contract=outputs,
            scope_fields=("corpus_id","project_id") if execute else (),revision_fields=(),capabilities=(),
            authority=ComponentAuthority.ARTIFACT_WRITE if execute else ComponentAuthority.TRANSFORMATION,
            side_effect_class=SideEffectClass.ARTIFACT_WRITE if execute else SideEffectClass.PURE_TRANSFORM,
            receipt=ReceiptDeclaration(mode=ReceiptMode.EMITTED,contract_id="execution_receipt") if execute else ReceiptDeclaration(mode=ReceiptMode.NONE))
    if name in ("CounterexampleFields", "SearchForCounterexamples", "CounterexampleOntology", "CounterexampleRetrieval", "CounterexampleStateView"):
        execute = name == "SearchForCounterexamples"
        contracts = {"CounterexampleFields":("native_flow_input","counterexample_request"),
            "SearchForCounterexamples":("counterexample_request","tool_result"),
            "CounterexampleOntology":("none","private_math_policy"),
            "CounterexampleRetrieval":("none","math_retrieval_policy"),
            "CounterexampleStateView":("tool_result","private_counterexample_state")}
        return ComponentManifest(component_id={"CounterexampleFields":"counterexample_fields",
            "SearchForCounterexamples":"search_for_counterexamples", "CounterexampleOntology":"counterexample_ontology",
            "CounterexampleRetrieval":"counterexample_retrieval", "CounterexampleStateView":"counterexample_state_view"}[name],
            version="1", input_contract=contracts[name][0], output_contract=contracts[name][1],
            scope_fields=("corpus_id","project_id") if execute else (), revision_fields=(),
            authority=ComponentAuthority.ARTIFACT_WRITE if execute else ComponentAuthority.TRANSFORMATION,
            side_effect_class=SideEffectClass.ARTIFACT_WRITE if execute else SideEffectClass.PURE_TRANSFORM,
            capabilities=(ComponentCapability.MODEL_CALL,) if execute else (),
            receipt=ReceiptDeclaration(mode=ReceiptMode.EMITTED,contract_id="execution_receipt") if execute
                else ReceiptDeclaration(mode=ReceiptMode.NONE))
    if name in ("EvidenceReadFields", "ReadEvidence"):
        read = name == "ReadEvidence"
        return ComponentManifest(component_id="read_evidence" if read else "evidence_read_fields", version="1",
            input_contract="read_evidence_request" if read else "native_flow_input",
            output_contract="tool_result" if read else "read_evidence_request",
            scope_fields=("corpus_id", "project_id") if read else (), revision_fields=(),
            authority=ComponentAuthority.READ_ONLY if read else ComponentAuthority.TRANSFORMATION,
            side_effect_class=SideEffectClass.READ_ONLY_RETRIEVAL if read else SideEffectClass.PURE_TRANSFORM,
            receipt=ReceiptDeclaration(mode=ReceiptMode.NONE))
    if name in ("ResearchRetrievalFields", "RetrieveResearchContext"):
        read = name == "RetrieveResearchContext"
        return ComponentManifest(component_id="retrieve_research_context" if read else "research_retrieval_fields", version="1",
            input_contract="research_retrieval_request" if read else "native_flow_input",
            output_contract="tool_result" if read else "research_retrieval_request",
            scope_fields=("corpus_id", "project_id") if read else (), revision_fields=(),
            authority=ComponentAuthority.ARTIFACT_WRITE if read else ComponentAuthority.TRANSFORMATION,
            side_effect_class=SideEffectClass.ARTIFACT_WRITE if read else SideEffectClass.PURE_TRANSFORM,
            capabilities=(ComponentCapability.MODEL_CALL,) if read else (),
            receipt=ReceiptDeclaration(mode=ReceiptMode.EMITTED, contract_id="execution_receipt") if read
                else ReceiptDeclaration(mode=ReceiptMode.NONE))
    if name in ("InspectCorpusFields", "InspectCorpusMetadata"):
        read = name == "InspectCorpusMetadata"
        return ComponentManifest(component_id="inspect_corpus_metadata" if read else "inspect_corpus_fields", version="1",
            input_contract="inspect_corpus_request" if read else "native_flow_input",
            output_contract="tool_result" if read else "inspect_corpus_request",
            scope_fields=("corpus_id", "project_id") if read else (), revision_fields=(),
            authority=ComponentAuthority.READ_ONLY if read else ComponentAuthority.TRANSFORMATION,
            side_effect_class=SideEffectClass.READ_ONLY_RETRIEVAL if read else SideEffectClass.PURE_TRANSFORM,
            receipt=ReceiptDeclaration(mode=ReceiptMode.NONE))
    sources = {"SourceFields": ("source_fields", "native_flow_input", "prepare_sources_request"),
        "PrepareSources": ("prepare_sources", "prepare_sources_request", "tool_result"),
        "EmbedSourceRegions": ("embed_source_regions", "tool_result", "tool_result"),
        "BuildSourceProjection": ("build_source_projection", "tool_result", "tool_result")}
    if name in sources:
        identifier, incoming, outgoing = sources[name]
        write = name != "SourceFields"
        return ComponentManifest(component_id=identifier, version="1", input_contract=incoming, output_contract=outgoing,
            scope_fields=("corpus_id", "project_id") if write else (), revision_fields=(),
            authority=ComponentAuthority.ARTIFACT_WRITE if write else ComponentAuthority.TRANSFORMATION,
            side_effect_class=SideEffectClass.ARTIFACT_WRITE if write else SideEffectClass.PURE_TRANSFORM,
            capabilities=(ComponentCapability.MODEL_CALL,) if name in ("PrepareSources", "EmbedSourceRegions") else (),
            receipt=ReceiptDeclaration(mode=ReceiptMode.EMITTED, contract_id="execution_receipt") if write
                else ReceiptDeclaration(mode=ReceiptMode.NONE))
    ontology = {
        "LoadOntologyFields": ("load_ontology_fields", "native_flow_input", "load_ontology_request"),
        "SaveOntologyFields": ("save_ontology_fields", "native_flow_input", "save_ontology_request"),
        "OntologyValidate": ("ontology_validate", "save_ontology_request", "ontology_validation_preview"),
        "OntologyLoad": ("ontology_load", "load_ontology_request", "tool_result"),
        "OntologySave": ("ontology_save", "ontology_validation_preview", "tool_result"),
    }
    if name in ontology:
        identifier, incoming, outgoing = ontology[name]
        write, read = name == "OntologySave", name == "OntologyLoad"
        return ComponentManifest(component_id=identifier, version="1", input_contract=incoming, output_contract=outgoing,
            scope_fields=("corpus_id", "project_id") if write or read else (),
            revision_fields=(), authority=ComponentAuthority.ARTIFACT_WRITE if write else
                ComponentAuthority.READ_ONLY if read else ComponentAuthority.TRANSFORMATION,
            side_effect_class=SideEffectClass.ARTIFACT_WRITE if write else
                SideEffectClass.READ_ONLY_RETRIEVAL if read else SideEffectClass.PURE_TRANSFORM,
            receipt=ReceiptDeclaration(mode=ReceiptMode.EMITTED, contract_id="execution_receipt") if write
                else ReceiptDeclaration(mode=ReceiptMode.NONE))
    if name == "ResearchRunFields":
        return ComponentManifest(component_id="research_run_fields", version="1",
            input_contract="research_run_fields", output_contract="research_run_request",
            scope_fields=(), revision_fields=(), authority=ComponentAuthority.TRANSFORMATION,
            side_effect_class=SideEffectClass.PURE_TRANSFORM, receipt=ReceiptDeclaration(mode=ReceiptMode.NONE))
    if name == "ResearchRunTool":
        return ComponentManifest(component_id="research_run_tool", version="1",
            input_contract="research_run_request", output_contract="tool_result",
            scope_fields=("corpus_id", "project_id"), revision_fields=(),
            authority=ComponentAuthority.ARTIFACT_WRITE, side_effect_class=SideEffectClass.ARTIFACT_WRITE,
            receipt=ReceiptDeclaration(mode=ReceiptMode.EMITTED, contract_id="execution_receipt"))
    if name in ("GuideFields", "ToolGuide"):
        return ComponentManifest(
            component_id="guide_fields" if name == "GuideFields" else "tool_guide", version="1",
            input_contract="tool_guide_fields" if name == "GuideFields" else "tool_guide_request",
            output_contract="tool_guide_request" if name == "GuideFields" else "tool_result",
            scope_fields=(), revision_fields=(), authority=ComponentAuthority.TRANSFORMATION,
            side_effect_class=SideEffectClass.PURE_TRANSFORM, receipt=ReceiptDeclaration(mode=ReceiptMode.NONE))
    if name in ("GraphContextPacket", "ValidateOKFSnapshot"):
        return ComponentManifest(component_id={"GraphContextPacket": "graph_context_packet", "ValidateOKFSnapshot": "validate_okf_snapshot"}[name],
            version="2", input_contract={"GraphContextPacket": "retrieval_context_packet_input", "ValidateOKFSnapshot": "okf_snapshot_validation_request"}[name],
            output_contract={"GraphContextPacket": "retrieval_context_packet", "ValidateOKFSnapshot": "okf_snapshot_validation_result"}[name],
            authority=ComponentAuthority.TRANSFORMATION, side_effect_class=SideEffectClass.PURE_TRANSFORM,
            receipt=ReceiptDeclaration(mode=ReceiptMode.NONE))
    manifest = _foundational_manifest(name)
    if manifest is None:
        raise ValueError(f"Unknown maintained component: {name}")
    return manifest

__all__ = [
    "ComponentAuthority",
    "ComponentExecutionContext",
    "ComponentManifest",
    "ComponentCapability",
    "ReceiptDeclaration",
    "ReceiptMode",
    "RevisionField",
    "SideEffectClass",
    "ScopeField",
    "validate_manifest_execution",
    "FOUNDATIONAL_COMPONENT_NAMES",
    "manifest_for_component",
    "validate_foundational_manifests",
]
