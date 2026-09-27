"""Lossless versioned NIMA graph profile within OKF v0.2 bundles."""
from urllib.parse import quote

from .models import identity, canonical, ConflictError
from .okf_contracts import GraphRevision, OKFDelta, OKFNode, OKFEdge, OKFSnapshot
from .okf_io import OKFBundle, OKFConceptDocument


def _concept_id(ref):
    # Encode dots too: concept IDs cannot have a filename suffix.
    def part(value):
        return quote(value, safe="").replace(".", "%2E")
    scope = "corpus" if ref.project_id is None else "projects/" + part(ref.project_id)
    return "nodes/" + part(ref.corpus_id) + "/" + scope + "/" + part(ref.local_id)


def _body(node, outgoing):
    lines = ["# " + str(node.properties.get("title", node.node_id)), "", "## Properties", "",
             "```json", canonical(node.properties).decode(), "```", "", "## Relations", ""]
    for edge in sorted(outgoing, key=lambda e: canonical(e.ref)):
        lines.append(f"- `{edge.relation}` → [{edge.target_id.local_id}](/{_concept_id(edge.target_id)}.md) (`{edge.edge_id}`)")
    return "\n".join(lines)


def snapshot_to_bundle(snapshot):
    snapshot = OKFSnapshot.model_validate(snapshot.model_dump(mode="json"))
    concepts = []
    for node in sorted(snapshot.nodes, key=lambda n: canonical(n.ref)):
        outgoing = tuple(edge for edge in snapshot.edges if edge.source_id == node.ref)
        concepts.append(OKFConceptDocument(
            concept_id=_concept_id(node.ref), type=node.node_type,
            title=str(node.properties.get("title", node.node_id)), body=_body(node, outgoing),
            extensions={"nima": {"node": node.model_dump(mode="json"),
                                 "relations": [e.model_dump(mode="json") for e in outgoing]}}))
    header = snapshot.model_dump(mode="json", exclude={"nodes", "edges"})
    manifest = {"version": 2, "snapshot": header,
                "content_hash": identity(snapshot),
                "node_order": [n.ref.model_dump(mode="json") for n in snapshot.nodes],
                "edge_order": [e.ref.model_dump(mode="json") for e in snapshot.edges],
                "concepts": sorted(c.concept_id for c in concepts)}
    return OKFBundle(concepts=tuple(concepts), index_metadata={"okf_version": "0.2", "nima": manifest})


def bundle_to_snapshot(bundle):
    bundle = OKFBundle.model_validate(bundle.model_dump(mode="json"))
    manifest = bundle.index_metadata.get("nima")
    if not isinstance(manifest, dict) or manifest.get("version") != 2:
        raise ValueError("NIMA graph import requires a version 2 bundle manifest")
    if bundle.index_metadata.get("okf_version") != "0.2":
        raise ValueError("unsupported OKF version")
    if manifest.get("concepts") != sorted(c.concept_id for c in bundle.concepts):
        raise ValueError("bundle concept inventory differs from manifest")
    nodes, edges = [], []
    for concept in bundle.concepts:
        metadata = concept.extensions.get("nima", {})
        node = OKFNode.model_validate(metadata.get("node"))
        outgoing = tuple(OKFEdge.model_validate(e) for e in metadata.get("relations", []))
        if concept.concept_id != _concept_id(node.ref) or concept.type != node.node_type:
            raise ValueError("concept identity or type differs from NIMA metadata")
        if concept.title != str(node.properties.get("title", node.node_id)):
            raise ValueError("concept title differs from NIMA metadata")
        if any(edge.source_id != node.ref for edge in outgoing):
            raise ValueError("edge stored under the wrong source concept")
        if concept.body.rstrip("\n") != _body(node, outgoing).rstrip("\n"):
            raise ValueError("inconsistent Markdown and NIMA relations/properties")
        nodes.append(node)
        edges.extend(outgoing)
    header = manifest.get("snapshot")
    if not isinstance(header, dict) or "nodes" in header or "edges" in header:
        raise ValueError("invalid NIMA snapshot manifest")
    node_lookup = {canonical(n.ref): n for n in nodes}
    edge_lookup = {canonical(e.ref): e for e in edges}
    if len(node_lookup) != len(nodes) or len(edge_lookup) != len(edges):
        raise ValueError("duplicate graph objects")
    nodes = [node_lookup[canonical(ref)] for ref in manifest["node_order"]]
    edges = [edge_lookup[canonical(ref)] for ref in manifest["edge_order"]]
    if len(nodes) != len(node_lookup) or len(edges) != len(edge_lookup):
        raise ValueError("inconsistent object inventory")
    snapshot = OKFSnapshot.model_validate({**header, "nodes": nodes, "edges": edges})
    # Ordering is part of the snapshot's canonical representation.
    if identity(snapshot) != manifest.get("content_hash"):
        raise ValueError("snapshot content hash differs from manifest")
    return snapshot


def store_snapshot_to_bundle(store, *, corpus_id, project_id=None):
    return snapshot_to_bundle(store.read_okf_snapshot(corpus_id=corpus_id, project_id=project_id))


def bundle_to_delta(bundle, *, delta_id, base_revision: GraphRevision, reason, current_snapshot=None):
    snapshot = bundle_to_snapshot(bundle)
    if (base_revision.corpus_id, base_revision.project_id) != (snapshot.corpus_id, snapshot.project_id):
        raise ConflictError("bundle and target revision scopes differ")
    corpus_nodes = [n for n in snapshot.nodes if n.project_id is None]
    corpus_edges = [e for e in snapshot.edges if e.project_id is None]
    if snapshot.project_id is not None and (corpus_nodes or corpus_edges):
        if current_snapshot is None or current_snapshot.graph_revision != base_revision:
            raise ConflictError("project bundle import requires the exact current view")
        current = {(type(item), item.ref): item for item in (*current_snapshot.nodes, *current_snapshot.edges)}
        for item in (*corpus_nodes, *corpus_edges):
            if current.get((type(item), item.ref)) != item:
                raise ConflictError("bundle changes corpus references; separate corpus approval required")
    return OKFDelta(delta_id=delta_id, base_revision=base_revision, corpus_id=snapshot.corpus_id,
                    project_id=snapshot.project_id, ontology_profile=snapshot.ontology_profile,
                    upsert_nodes=tuple(n for n in snapshot.nodes if n.project_id == snapshot.project_id),
                    add_edges=tuple(e for e in snapshot.edges if e.project_id == snapshot.project_id), reason=reason)
