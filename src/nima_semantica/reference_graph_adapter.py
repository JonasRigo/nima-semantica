"""Explicit conversion of the reference extraction schema, without graph writes."""
from .evidence_contracts import validate_reference
from .models import identity
from .okf_contracts import EvidenceReference, OKFDelta, OKFNode, OKFEdge
from .ontology_services import OntologyService
from .source_corpus import SourceRegion


def reference_graph_delta(raw, context, store):
    request = context.request
    if request.project_id is None or request.graph_revision is None or not request.ontology_profile:
        raise ValueError("graph conversion requires project, graph revision, and ontology")
    if store.graph_revision(request.corpus_id, request.project_id) != request.graph_revision:
        raise ValueError("stale graph revision")
    objects = raw.get("objects", [raw])
    if not isinstance(objects, list) or len(objects) != 1:
        raise ValueError("reference preset requires exactly one document-level graph")
    graph = objects[0]
    if not isinstance(graph, dict) or not graph.get("components"):
        raise ValueError("reference preset requires document-level components")
    if graph.get("imported_documents"):
        raise ValueError("imported documents need explicit ontology alignment")
    ontology = OntologyService()
    profile = ontology.resolve(request.ontology_profile)

    def evidence(chunk_ids):
        if not chunk_ids:
            raise ValueError("missing source chunk references")
        refs = []
        for chunk in dict.fromkeys(chunk_ids):
            region_id = context.chunk_bindings.get(chunk)
            if region_id is None or region_id not in context.source_region_ids:
                raise ValueError(f"unbound source chunk: {chunk}")
            record = store.get(region_id, corpus_id=request.corpus_id)
            if record is None or record.kind != "SourceRegion":
                raise ValueError("source region missing")
            region = SourceRegion.model_validate(record.content)
            ref = EvidenceReference(corpus_id=region.corpus_id, project_id=region.project_id,
                artifact_id=region.artifact_id, region_id=region_id, source_revision=region.source_revision,
                content_hash=region.artifact_id, locator={"start": region.start, "end": region.end},
                quotation=region.text if len(region.text) <= 20_000 else None)
            if validate_reference(store, ref, corpus_id=request.corpus_id,
                                  project_id=request.project_id, target_id=request.request_id):
                raise ValueError("source evidence validation failed")
            refs.append(ref)
        return tuple(refs)

    nodes = []
    by_id = {}
    for key, identifier, node_type in (("components", "component_id", "claim"),
                                       ("obligations", "obligation_id", "obligation")):
        for item in graph.get(key, []):
            local_id = item[identifier]
            if local_id in by_id:
                raise ValueError("duplicate extraction identifier")
            node = OKFNode(node_id=identity({"request": request.request_id, "foreign_id": local_id}),
                node_type=node_type, corpus_id=request.corpus_id, project_id=request.project_id,
                properties={"source_assessment": item}, evidence=evidence(item.get("source_chunk_ids")),
                ontology_profile=profile.digest, producer="reference_graph_adapter")
            nodes.append(node)
            by_id[local_id] = node
    edges = []
    for ordinal, item in enumerate(graph.get("relations", [])):
        source, target = by_id.get(item["subject_id"]), by_id.get(item["object_id"])
        if source is None or target is None:
            raise ValueError("unresolved relation endpoint")
        # The original reference schema has no relation evidence field. Require
        # an explicit operator binding; endpoint evidence is not relation evidence.
        chunks = context.relation_bindings.get(str(ordinal), ())
        edges.append(OKFEdge(edge_id=identity({"request": request.request_id, "relation": ordinal}),
            relation={"depends_on": "requires"}.get(item["predicate"], item["predicate"]),
            source_id=source.ref, target_id=target.ref, corpus_id=request.corpus_id,
            project_id=request.project_id, evidence=evidence(chunks),
            ontology_profile=profile.digest, properties={"source_assessment": item}))
    delta = OKFDelta(delta_id=identity({"request": request.request_id, "raw": raw}),
        base_revision=request.graph_revision, corpus_id=request.corpus_id,
        project_id=request.project_id, ontology_profile=profile.digest,
        upsert_nodes=nodes, add_edges=edges, reason="Imported extraction proposal; source statuses are not verification.")
    report = ontology.validate_delta(delta)
    if not report.valid:
        raise ValueError("extraction is incompatible with selected ontology")
    return delta
