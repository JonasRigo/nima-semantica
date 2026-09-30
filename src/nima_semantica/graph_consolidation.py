"""Revision-pinned, provenance-preserving consolidation of saved graph candidates.

Semantic decisions belong to the harness. This module never merges by label,
invents relations, commits changes, or treats connectivity as scientific validity.
"""
from collections import defaultdict
from .models import NimaError, ConflictError, canonical, identity
from .okf_contracts import OKFDelta, OKFNode, OKFEdge
from .artifact_contracts import ArtifactEnvelope, GraphArtifact
from .artifact_service import ArtifactService
from .proposal_service import ProposalService
from .ontology_services import OntologyService
from .evidence_contracts import validate_delta_evidence
from .execution_receipts import ExecutionReceiptService
from .receipts import ExecutionReceipt
from .tool_contracts import ToolResult
from .source_quality import provisional_object, PROVISIONAL, WARNING
from .research_run_service import ResearchRunService

VERSION = "graph-consolidation-v1"
MAX_NODES = 10000
MAX_EDGES = 20000
MAX_BYTES = 32_000_000


def unique(items, key=identity):
    values = {}
    for item in items:
        fingerprint = key(item)
        if fingerprint in values and values[fingerprint] != item:
            raise ConflictError("Conflicting provenance for the same identity; reconcile the source binding first")
        values[fingerprint] = item
    return tuple(values[k] for k in sorted(values))


def provenance(items):
    return unique(items, lambda p: str((p.reference_kind, p.target_id, p.revision)))


def components(nodes, edges):
    neighbors = {n.ref: set() for n in nodes.values()}
    for edge in edges.values():
        a, b = edge.source_id, edge.target_id
        if a in neighbors and b in neighbors:
            neighbors[a].add(b); neighbors[b].add(a)
    unseen = set(neighbors); sizes = []
    while unseen:
        pending = [next(iter(unseen))]; count = 0
        while pending:
            node = pending.pop()
            if node not in unseen:
                continue
            unseen.remove(node); count += 1; pending.extend(neighbors[node] & unseen)
        sizes.append(count)
    return {"connected_components": len(sizes), "component_sizes": sorted(sizes, reverse=True),
            "isolated_nodes": sum(n == 1 for n in sizes), "connectivity_is_not_verification": True}


def load_selection(store, request, context):
    scope = dict(corpus_id=context.corpus_id, project_id=context.project_id)
    if store.graph_revision(**scope) != request.graph_revision:
        raise ConflictError("Graph revision changed; re-plan against Inspect Corpus before reconciling")
    if request.run_id and ResearchRunService(store).get_run(request.run_id, **scope) is None:
        raise ConflictError("Run is outside the selected scope")
    for ref in (request.target_record_id, request.parent_record_id):
        record = store.get(ref, **scope) if ref else None
        if ref and (record is None or record.project_id != context.project_id):
            raise ConflictError("Progress reference is outside the selected project")
    selection = request.consolidation
    ontology = OntologyService.from_store(store, **scope)
    profile = ontology.resolve(request.ontology_profile)
    nodes, edges, origins, leaves, issues = {}, {}, {}, set(), []
    snapshot = store.read_okf_snapshot(**scope)
    existing = {n.node_id: n for n in snapshot.nodes if n.project_id == context.project_id}
    base_edges = {e.edge_id: e for e in snapshot.edges if e.project_id == context.project_id}
    if selection.include_project_graph:
        for n in existing.values():
            if ontology.resolve(n.ontology_profile).digest == profile.digest:
                nodes[n.node_id] = n
        selected_refs = {n.ref for n in nodes.values()}
        edges.update({k: e for k, e in base_edges.items() if e.source_id in selected_refs and e.target_id in selected_refs})
    sources = set(); byte_count = 0
    for artifact_id in sorted(selection.artifact_ids):
        env, blob = ArtifactService(store).read(artifact_id, **scope)
        byte_count += len(blob)
        if byte_count > MAX_BYTES:
            raise NimaError("Consolidation inputs exceed 32 MB; select the latest consolidated artifact and a smaller set of new candidates")
        if env.project_id != context.project_id or env.artifact_kind != "graph_candidate" or env.status != "proposed":
            raise NimaError("Select proposed graph candidates from this project only")
        delta = OKFDelta.model_validate_json(blob)
        if (delta.corpus_id, delta.project_id) != (context.corpus_id, context.project_id):
            raise ConflictError("Candidate graph scope differs from its envelope")
        if delta.remove_node_ids or delta.remove_edge_ids:
            raise NimaError("Deletion proposals cannot be consolidation inputs; approve/reconcile them separately")
        if ontology.resolve(delta.ontology_profile).digest != profile.digest:
            raise NimaError("Input ontologies differ; choose compatible candidates or explicitly migrate ontology first")
        report = validate_delta_evidence(store, delta)
        if not report.valid:
            raise NimaError("Input candidate evidence does not validate: " + report.issues[0].code)
        prior = delta.metadata.get("consolidation", {}) if delta.producer == VERSION else {}
        incoming_leaves = set(prior.get("leaf_artifact_ids", [artifact_id]))
        if leaves & incoming_leaves:
            raise NimaError("Overlapping consolidation inputs; use the latest consolidated candidate without re-adding its source candidates")
        leaves.update(incoming_leaves)
        issues.extend(prior.get("unresolved", env.content.get("unresolved", [])))
        sources.update(env.source_artifact_ids)
        renamed = {n.node_id: n.node_id if prior else "cg-" + identity((artifact_id, n.node_id))[:32] for n in delta.upsert_nodes}
        renamed_refs = {n.ref: n.ref.model_copy(update={"local_id": renamed[n.node_id]}) for n in delta.upsert_nodes}
        for n in delta.upsert_nodes:
            key = renamed[n.node_id]
            parents = tuple(renamed_refs.get(p, p) for p in n.parents)
            item = n.model_copy(update={"node_id": key, "parents": parents})
            if key in nodes and nodes[key] != item:
                raise ConflictError("Candidate differs from the current node with the same canonical ID; re-plan without stale inputs")
            nodes[key] = item
            origins[key] = list(prior.get("origins", {}).get(key, [{"artifact_id": artifact_id, "node_id": n.node_id}]))
        for e in delta.add_edges:
            if e.source_id not in renamed_refs or e.target_id not in renamed_refs:
                raise NimaError("Candidate must include both endpoints; consolidate self-contained candidates")
            key = e.edge_id if prior else "ce-" + identity((artifact_id, e.edge_id))[:32]
            item = e.model_copy(update={"edge_id": key,
                "source_id": renamed_refs[e.source_id], "target_id": renamed_refs[e.target_id]})
            if key in edges and edges[key] != item:
                raise ConflictError("Conflicting canonical edge; re-plan current inputs")
            edges[key] = item
    if len(nodes) > MAX_NODES or len(edges) > MAX_EDGES:
        raise NimaError("Consolidation exceeds 10000 nodes/20000 edges; narrow the explicitly selected research scope")
    selected_refs = {n.ref for n in nodes.values()}
    if any(p not in selected_refs for n in nodes.values() for p in n.parents):
        raise NimaError("Consolidation requires self-contained project candidates, including all parent dependencies; corpus and project names are distinct identities")
    plan_hash = identity({"version": VERSION, "inputs": sorted(selection.artifact_ids), "revision": request.graph_revision,
        "ontology": profile.digest, "include_project_graph": selection.include_project_graph, "nodes": nodes, "edges": edges})
    return scope, profile, nodes, edges, existing, base_edges, origins, leaves, issues, sources, plan_hash


def consolidate_graph(store, request, context):
    if store is None:
        return ToolResult(operation="Deep Extraction", status="unavailable", diagnostics=({"code": "consolidation.store_missing"},))
    selection = request.consolidation
    scope = dict(corpus_id=context.corpus_id, project_id=context.project_id)
    receipts = ExecutionReceiptService(store)
    rid = identity((VERSION, scope, request.operation_id))
    fingerprint = identity({"version": VERSION, "request": request, "scope": scope})
    if request.mode == "consolidate":
        if not context.allow_audit_writes:
            return ToolResult(operation="Deep Extraction", status="failed", diagnostics=({"code": "consolidation.write_not_authorized"},))
    try:
        if request.mode == "consolidate":
            previous = receipts.replay(rid, request_hash=fingerprint, **scope)
            if previous:
                return ToolResult.model_validate(previous.metadata["result"])
        scope, profile, nodes, edges, existing, base_edges, origins, leaves, issues, sources, plan_hash = load_selection(store, request, context)
        if selection.plan_hash and selection.plan_hash != plan_hash:
            raise ConflictError("Consolidation plan changed; request consolidation_plan again and review decisions")
        before = components(nodes, edges)
        if request.mode == "consolidation_plan":
            ordered = [n for _, n in sorted(nodes.items()) if not selection.query or selection.query.casefold() in str(n.properties).casefold()]
            page = ordered[selection.offset:selection.offset + selection.limit]
            matching = {n.node_id for n in ordered}
            ordered_edges = [e for _,e in sorted(edges.items()) if not selection.query
                or e.source_id.local_id in matching or e.target_id.local_id in matching]
            edge_page = ordered_edges[selection.edge_offset:selection.edge_offset + selection.edge_limit]
            next_request = None
            if selection.offset + len(page) < len(ordered) or selection.edge_offset + len(edge_page) < len(ordered_edges):
                next_request = request.model_dump(mode="json", exclude_none=True)
                next_request["consolidation"].update(offset=selection.offset + len(page),
                    edge_offset=selection.edge_offset + len(edge_page), plan_hash=plan_hash)
            return ToolResult(operation="Deep Extraction", status="complete", data={"executed": False, "plan_hash": plan_hash,
                "graph_revision": request.graph_revision.model_dump(mode="json"), "ontology": profile.model_dump(mode="json"),
                "total_nodes": len(nodes), "total_edges": len(edges), "matching_nodes": len(ordered),
                "matching_edges": len(ordered_edges), "structure": before,
                "nodes": [{"node_id": n.node_id, "node_type": n.node_type,
                    "properties": {k: v for k, v in n.properties.items() if k != "consolidation_variants"},
                    "variants_in_artifact": "consolidation_variants" in n.properties,
                    "parents": [p.model_dump(mode="json") for p in n.parents],
                    "evidence": [{"region_id": e.region_id, "content_hash": e.content_hash, "locator": e.locator} for e in n.evidence], "origins": origins.get(n.node_id, []),
                    "existing_project_node": n.node_id in existing} for n in page],
                "edges": [{"edge_id": e.edge_id, "relation": e.relation,
                    "source_id": e.source_id.local_id, "target_id": e.target_id.local_id,
                    "properties": e.properties, "region_ids": [v.region_id for v in e.evidence]} for e in edge_page],
                "next_request": next_request, "unresolved": issues,
                "next": "Read evidence and inspect all relevant pages (query filters across all nodes). Submit explicit same-type identity merges and ontology-valid links with rationale via consolidate and this plan_hash. Labels alone do not establish equivalence. Reuse the result artifact with new candidates for incremental consolidation."})
        mapping = {key: key for key in nodes}; used = set()
        for merge in selection.merges:
            members = set(merge.node_ids)
            if not members <= nodes.keys() or used & members:
                raise NimaError("Unknown or overlapping merge members; use disjoint IDs from the pinned plan")
            if len({nodes[key].node_type.casefold() for key in members}) != 1:
                raise NimaError("Different node types cannot be merged; propose a typed relation instead")
            old = members & existing.keys()
            if old and (len(old) > 1 or merge.canonical_id not in old):
                raise NimaError("Preserve the existing project identity; merging multiple admitted identities requires a separate explicit correction")
            selected = [nodes[k] for k in sorted(members)]
            retained = nodes[merge.canonical_id]
            properties = dict(retained.properties)
            properties["consolidation_variants"] = [
                {"node_id": n.node_id, "properties": n.properties, "status": n.status.value} for n in selected]
            properties["identity_rationale"] = merge.rationale
            if any(provisional_object(n) for n in selected):
                properties.update(preparation_quality=PROVISIONAL, warning=WARNING)
            merged = OKFNode.model_validate(retained.model_dump(mode="json") | {
                "properties": properties, "status": "proposed", "evidence": unique(e for n in selected for e in n.evidence),
                "provenance": provenance(p for n in selected for p in n.provenance),
                "parents": unique(p for n in selected for p in n.parents)})
            nodes[merge.canonical_id] = merged
            origins[merge.canonical_id] = [v for key in sorted(members) for v in origins.get(key, [])]
            for key in members:
                mapping[key] = merge.canonical_id
                if key != merge.canonical_id:
                    del nodes[key]; origins.pop(key, None)
            used.update(members)
        for key, n in list(nodes.items()):
            nodes[key] = n.model_copy(update={"parents": unique(p.model_copy(update={"local_id": mapping.get(p.local_id, p.local_id)}) for p in n.parents)})
        for key, e in list(edges.items()):
            edges[key] = e.model_copy(update={"source_id": e.source_id.model_copy(update={"local_id": mapping[e.source_id.local_id]}),
                "target_id": e.target_id.model_copy(update={"local_id": mapping[e.target_id.local_id]})})
        for link in selection.links:
            a, b = mapping.get(link.source_id), mapping.get(link.target_id)
            if a not in nodes or b not in nodes:
                raise NimaError("Link endpoint was not in the pinned plan")
            available = unique(e for n in (nodes[a], nodes[b]) for e in n.evidence)
            refs = tuple(e for e in available if e.region_id in link.region_ids)
            if set(link.region_ids) != {e.region_id for e in refs}:
                raise NimaError("New links must cite exact prepared evidence from their endpoints")
            key = "cl-" + identity((a, b, link.relation, link.rationale, link.region_ids))[:32]
            properties = {"rationale": link.rationale, "semantic_status": "harness_proposed_unverified"}
            if any(provisional_object(nodes[k]) for k in (a, b)):
                properties.update(preparation_quality=PROVISIONAL, warning=WARNING)
            edges[key] = OKFEdge(edge_id=key, relation=link.relation, source_id=nodes[a].ref, target_id=nodes[b].ref,
                **scope, ontology_profile=profile.digest, evidence=refs, properties=properties, producer=VERSION)
        # Preserve distinct relation assertions; collapse only exact duplicates.
        dedup = {}
        for key, edge in sorted(edges.items()):
            signature = identity(edge.model_dump(mode="json", exclude={"edge_id"}))
            dedup.setdefault(signature, edge)
        edges = {e.edge_id: e for e in dedup.values()}
        if len(edges) > MAX_EDGES:
            raise NimaError("Consolidated candidate exceeds 20000 edges; narrow scope without discarding evidence")
        issues = list(dict.fromkeys([*issues, *selection.unresolved]))
        if not nodes:
            issues.append("Selected candidates contain no nodes; no cohesive graph was produced.")
        metadata = {"unresolved": issues, "consolidation": {"input_artifact_ids": sorted(selection.artifact_ids), "leaf_artifact_ids": sorted(leaves),
            "origins": origins, "decisions": selection.model_dump(mode="json", exclude={"artifact_ids"}),
            "unresolved": issues, "before": before, "after": components(nodes, edges), "source_coverage_certified": False}}
        delta = OKFDelta(delta_id=request.operation_id, base_revision=request.graph_revision, **scope,
            ontology_profile=profile.digest, upsert_nodes=tuple(nodes[k] for k in sorted(nodes)),
            add_edges=tuple(edges[k] for k in sorted(edges)), reason="Explicit cross-batch/paper reconciliation; proposal only.",
            producer=VERSION, producer_version="1", metadata=metadata)
        if len(canonical(delta)) > MAX_BYTES:
            raise NimaError("Consolidated candidate exceeds 32 MB; narrow scope without discarding evidence")
        validation = OntologyService.from_store(store, **scope).validate_delta(delta)
        if not validation.valid:
            raise NimaError("Consolidation violates ontology: " + validation.issues[0].message)
        evidence = validate_delta_evidence(store, delta)
        if not evidence.valid:
            raise NimaError("Consolidation evidence invalid: " + evidence.issues[0].code)
        all_refs = {n.ref for n in nodes.values()}
        if any(p not in all_refs for n in nodes.values() for p in n.parents):
            raise NimaError("Consolidation has missing parent dependencies")
        data = canonical(delta); digest = identity(delta)
        regions = tuple(sorted({e.region_id for n in (*delta.upsert_nodes, *delta.add_edges) for e in n.evidence if e.region_id}))
        envelope = ArtifactEnvelope(artifact_id=digest, content_hash=digest, artifact_kind="graph_candidate", media_type="application/json",
            **scope, source_artifact_ids=tuple(sorted(sources)), provenance=regions, status="proposed",
            content={"ontology_profile": profile.digest, "unresolved": issues, "graph_revision": request.graph_revision.model_dump(mode="json"),
                     "consolidation_inputs": sorted(selection.artifact_ids)})
        artifact = GraphArtifact(envelope=envelope, delta=delta)
        result = ToolResult(operation="Deep Extraction", status="partial" if issues else "complete",
            data={"executed": True, "committed": False, "plan_hash": plan_hash, "node_count": len(nodes), "edge_count": len(edges),
                "structure": metadata["consolidation"]["after"], "unresolved": issues,
                "source_coverage_certified": False, "next": "Review this consolidated graph candidate with Analyze Graph and prepare its exact delta for approval; do not claim scientific verification or complete source coverage."},
            artifacts={"graph_proposal": digest}, receipt_ids=(rid,))
        with store.joined_transaction():
            if store.graph_revision(**scope) != request.graph_revision:
                raise ConflictError("Graph changed during consolidation; re-plan")
            from .deep_extraction_tool import _revision
            revision_id = identity((rid, digest, "registry"))
            _revision(store, context.corpus_id, revision_id, (digest,))
            ArtifactService(store).publish(data, envelope, registry_revision=revision_id)
            ProposalService(store).persist_graph_candidate(artifact, source_region_ids=regions)
            receipts.record(ExecutionReceipt(receipt_id=rid, operation_id=request.operation_id, stage=VERSION, **scope,
                run_id=request.run_id, status="partial" if issues else "completed", tool_version=VERSION,
                metadata={"request_hash": fingerprint, "result": result.model_dump(mode="json")}))
        return result
    except (NimaError, ValueError) as exc:
        return ToolResult(operation="Deep Extraction", status="failed", data={"executed": False, "committed": False},
            diagnostics=({"code": "consolidation.invalid_or_stale", "message": str(exc),
                "next_action": "Correct the selected scope/decisions and re-plan; do not retry unchanged or discard provenance."},))
