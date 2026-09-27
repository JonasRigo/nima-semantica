"""Vector-first selection with explicit graph branch and recorded omissions."""
import json
import re
import hashlib

import numpy as np

from .corpus import validate_vectors
from .models import ConflictError, ConfigurationError, NimaError, Record, canonical, identity
from .retrieval_contracts import (
    EmbeddingProjectionManifest,
    RetrievalContextPacket,
    RetrievalSeed,
)
from .storage import atomic_bytes


def _file_hash(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def expand_tex_references(selected, regions, *, max_additional=24, max_hops=3, max_added_chars=100000):
    """Bounded, document-local TeX definition/reference retrieval, never compilation.

    Preserve exact source regions; report duplicate/missing labels and omissions.
    Include neighboring chunks because definitions can cross chunk boundaries.
    """
    if min(max_additional, max_hops, max_added_chars) < 0:
        raise ValueError("negative cross-reference budget")
    by_id = {region["id"]: region for region in regions}
    labels, neighbors = {}, {}
    def uncomment(text):
        return re.sub(r"(?<!\\)%[^\n]*", "", text)
    for region in regions:
        document = region["document_id"]
        neighbors[(document, region["ordinal"])] = region["id"]
        for label in re.findall(r"\\label\s*\{([^{}]+)\}", uncomment(region["text"])):
            labels.setdefault((document, label.strip()), set()).add(region["id"])
    result = list(selected)
    seen = {region["id"] for region in selected}
    frontier = list(selected)
    evidence, unresolved, omitted = [], [], []
    added_chars = 0
    documents = {region["document_id"] for region in selected}
    # A theorem can use a defined symbol without an explicit \ref. Seed formal
    # definition environments from the same documents before following citations.
    definitions = sorted((region for region in regions if region["document_id"] in documents
        and re.search(r"\\begin\s*\{definition\*?\}", uncomment(region["text"]))),
        key=lambda region: (region["document_id"], region["ordinal"]))
    for definition in definitions:
        targets = [definition["id"]] + [neighbors[(definition["document_id"], definition["ordinal"] + offset)]
            for offset in (-1, 1) if (definition["document_id"], definition["ordinal"] + offset) in neighbors]
        for candidate_id in targets:
            if candidate_id in seen:
                continue
            candidate = by_id[candidate_id]
            if len(result) - len(selected) >= max_additional or added_chars + len(candidate["text"]) > max_added_chars:
                omitted.append(candidate_id)
                continue
            seen.add(candidate_id)
            result.append(candidate)
            frontier.append(candidate)
            added_chars += len(candidate["text"])
            evidence.append({"reason": "source-definition-environment", "target": definition["id"],
                "included": candidate_id, "neighbor": candidate_id != definition["id"], "hop": 0})
    for hop in range(max_hops):
        following = []
        for region in frontier:
            references = re.findall(r"\\(?:[cC]ref|[cC]pageref|eqref|ref|autoref)\*?\s*\{([^{}]+)\}", uncomment(region["text"]))
            for reference in references:
                for label in (item.strip() for item in reference.split(",")):
                    matches = labels.get((region["document_id"], label), set())
                    if len(matches) != 1:
                        unresolved.append({"from": region["id"], "label": label, "reason": "missing" if not matches else "ambiguous"})
                        continue
                    target_id = next(iter(matches))
                    target = by_id[target_id]
                    targets = [target_id] + [neighbors[(target["document_id"], target["ordinal"] + offset)]
                        for offset in (-1, 1) if (target["document_id"], target["ordinal"] + offset) in neighbors]
                    for candidate_id in targets:
                        if candidate_id in seen:
                            continue
                        candidate = by_id[candidate_id]
                        if len(result) - len(selected) >= max_additional or added_chars + len(candidate["text"]) > max_added_chars:
                            omitted.append(candidate_id)
                            continue
                        seen.add(candidate_id)
                        result.append(candidate)
                        following.append(candidate)
                        added_chars += len(candidate["text"])
                        evidence.append({"from": region["id"], "label": label, "target": target_id,
                            "included": candidate_id, "neighbor": candidate_id != target_id, "hop": hop + 1})
        frontier = following
        if not frontier:
            break
    return result, {"method": "document-local-tex-definitions-and-references-v1", "edges": evidence,
        "unresolved": unresolved, "omitted": sorted(set(omitted)), "max_hops": max_hops,
        "hop_limit_reached": bool(frontier), "max_additional": max_additional,
        "max_added_chars": max_added_chars, "added_chars": added_chars}


def _vector_index(store, manifest, corpus_id, project_id, *, prepared_only=False):
    """Build once per embedding generation; warm queries touch no matrices."""
    from semantica.vector_store.faiss_store import FAISSIndex
    import faiss

    generation = store.embedding_revision(corpus_id)
    allowed = (store.preparation_membership(corpus_id, project_id) or frozenset()) if prepared_only else None
    if prepared_only and not allowed:
        raise ConfigurationError("no prepared regions in requested corpus/project scope")
    embedding_manifest = manifest.model_dump(mode="json")
    key = (corpus_id, project_id, identity(embedding_manifest), generation, prepared_only)
    cached = store._vector_cache.get(key)
    if cached is not None:
        return cached
    descriptor = {"version": 3, "corpus_id": corpus_id, "project_id": project_id,
                  "preparation_membership": "required" if allowed is not None else "legacy",
                  "generation": generation, "embedding_manifest": embedding_manifest, "metric": "cosine"}
    directory = store.root / "indexes" / identity(descriptor)
    index_path, manifest_path = directory / "index.faiss", directory / "manifest.json"
    if manifest_path.exists():
        saved = json.loads(manifest_path.read_bytes())
        sidecar = directory / "index.faiss.meta.json"
        if saved["manifest"] != descriptor or not index_path.exists() or saved["index_hash"] != _file_hash(index_path) or not sidecar.exists() or saved.get("sidecar_hash") != _file_hash(sidecar):
            raise ConfigurationError("vector cache corrupted; explicit rebuild required")
        index = FAISSIndex.load(index_path, dimension=manifest.dimension)
        ids = saved["ids"]
        if index.vector_ids != ids or index.index.ntotal != len(ids) or index.index.d != manifest.dimension:
            raise ConfigurationError("vector cache ordering mismatch")
    else:
        batches = [(key, record) for key, record in store.records("EmbeddingBatch", corpus_id=corpus_id, project_id=project_id) if record.project_id in (None, project_id)]
        if not batches:
            raise ConfigurationError("hybrid retrieval requires an ingested vector corpus")
        ids, seen = [], set()
        index = FAISSIndex(faiss.IndexFlatIP(manifest.dimension), manifest.dimension)
        for _, batch in batches:
            if allowed is not None and not any(region_id in allowed for region_id in batch.content["region_ids"]):
                continue
            if batch.content["manifest"] != embedding_manifest:
                raise ConfigurationError("embedding manifest mismatch; explicit corpus reindex required")
            matrix = validate_vectors(json.loads(store.read_artifact(batch.content["matrix_artifact"])),
                                      len(batch.content["region_ids"]), manifest)
            positions, batch_ids = [], []
            for position, region_id in enumerate(batch.content["region_ids"]):
                if allowed is not None and region_id not in allowed:
                    continue
                region = store.get(region_id, corpus_id=corpus_id)
                if region is None or region.kind != "SourceRegion":
                    raise NimaError("embedding refers to missing or out-of-scope region")
                if region.project_id not in (None, project_id):
                    continue
                if region_id not in seen:
                    seen.add(region_id)
                    positions.append(position)
                    batch_ids.append(region_id)
            if positions:
                rows = np.ascontiguousarray(matrix[positions])
                faiss.normalize_L2(rows)
                # Deduplication uses the persistent build-time set above.
                # The wrapper's per-add set reconstruction is quadratic for many batches.
                index.index.add(rows)
                index.vector_ids.extend(batch_ids)
                ids.extend(batch_ids)
        if not ids:
            raise ConfigurationError("no vectors in requested scope")
        directory.mkdir(parents=True, exist_ok=True)
        index.save(index_path)
        saved = {"manifest": descriptor, "ids": ids, "index_hash": _file_hash(index_path),
                 "sidecar_hash": _file_hash(directory / "index.faiss.meta.json")}
        atomic_bytes(manifest_path, canonical(saved))
    # Keep one generation per scope/profile; bound resident corpus indexes too.
    for previous in list(store._vector_cache):
        if previous[:2] == key[:2]:
            del store._vector_cache[previous]
    while len(store._vector_cache) >= 2:
        del store._vector_cache[next(iter(store._vector_cache))]
    # Receipts reference the immutable disk manifest, avoiding an O(corpus) copy.
    receipt = {"manifest": descriptor, "index_hash": saved["index_hash"],
               "manifest_artifact": store.artifact(canonical(saved)), "vector_count": len(ids)}
    cached = (index, ids, receipt)
    store._vector_cache[key] = cached
    return cached


def retrieve(store, provider, profile, question, project_id, corpus_id, max_hops=2, limit=8,
             *, max_nodes=256, max_edges=1024, max_neighbors=64, max_results=64,
             max_omitted=256, prepared_only=False):
    """Scoped vector seeds and bounded citation/structural traversal.

    Omission IDs are a bounded sample; omitted_count remains exact for indexed
    regions. graph_edges identifies actual typed links and their receipts.
    """
    import faiss
    if not isinstance(corpus_id, str) or not corpus_id:
        raise ConfigurationError("retrieval requires an explicit corpus scope")
    if prepared_only and not project_id:
        raise ConfigurationError("prepared retrieval requires an explicit project scope")
    if min(max_hops, limit, max_nodes, max_edges, max_neighbors, max_results, max_omitted) < 0 or not limit or not max_results or not max_nodes:
        raise ValueError("invalid retrieval budget")
    # Pin the generation and metadata through selection, including concurrent readers.
    with store._mutex:
        store_revision = store.revision
        graph_revision = store.graph_revision(corpus_id, project_id)
        snapshot = store.read_okf_snapshot(corpus_id=corpus_id, project_id=project_id, revision=graph_revision)
        graph_nodes = {"okf:" + identity(n.ref): n for n in snapshot.nodes}
        graph_links = {}
        def link(source, target, relation, edge_id=None):
            graph_links.setdefault(source, []).append({"id": target, "relation": relation,
                "receipt_id": None, "source": source, "target": target, "edge_id": edge_id})
        for key, node in graph_nodes.items():
            for evidence in node.evidence:
                link(evidence.region_id, key, "grounds")
                link(key, evidence.region_id, "source_evidence")
        for edge in snapshot.edges:
            source, target = "okf:" + identity(edge.source_id), "okf:" + identity(edge.target_id)
            link(source, target, edge.relation, edge.ref.model_dump(mode="json"))
            link(target, source, edge.relation, edge.ref.model_dump(mode="json"))
        vectors, manifest = provider.embed_query(profile, question)
        query = validate_vectors(vectors, 1, manifest)
        index, ids, index_receipt = _vector_index(store, manifest, corpus_id, project_id, prepared_only=prepared_only)
        allowed = (store.preparation_membership(corpus_id, project_id) or frozenset()) if prepared_only else None
        faiss.normalize_L2(query)
        scores, indices = index.index.search(query, min(limit, max_results, max_nodes, len(ids)))
        hits = [{"region_id": ids[int(position)], "vector_score": float(score), "graph_paths": [], "graph_edges": []}
                for score, position in zip(scores[0], indices[0]) if position >= 0]
        visited = {hit["region_id"] for hit in hits}
        frontier = [(hit["region_id"], [hit["region_id"]], []) for hit in hits]
        traversed = 0
        truncated = False
        for _ in range(max_hops):
            following = []
            for node_id, path, evidence in frontier:
                remaining = max_edges - traversed
                if remaining <= 0 or len(visited) >= max_nodes or len(hits) >= max_results:
                    truncated = True
                    break
                edges = list(graph_links.get(node_id, ()))
                if node_id not in graph_nodes:
                    edges += store.neighbors(node_id, corpus_id=corpus_id, project_id=project_id,
                                             limit=min(max_neighbors, remaining) + 1)
                cap = min(max_neighbors, remaining)
                if len(edges) > cap:
                    truncated = True
                    edges = edges[:cap]
                edges += [{"id": neighbor, "relation": "adjacent_region", "receipt_id": None}
                          for neighbor in store.region_neighbors(node_id, corpus_id=corpus_id, project_id=project_id)]
                for edge in edges:
                    if traversed >= max_edges or len(visited) >= max_nodes or len(hits) >= max_results:
                        truncated = True
                        break
                    traversed += 1
                    neighbor = edge["id"]
                    if neighbor in visited:
                        continue
                    record = store.get(neighbor, corpus_id=corpus_id, project_id=project_id) if neighbor not in graph_nodes else None
                    if record is not None and record.project_id not in (None, project_id):
                        continue
                    if record is None and neighbor not in graph_nodes:
                        continue
                    if record is not None and record.kind == "SourceRegion" and allowed is not None and neighbor not in allowed:
                        continue
                    visited.add(neighbor)
                    new_path = path + [neighbor]
                    new_evidence = evidence + [{"source": edge.get("source", node_id), "target": edge.get("target", neighbor),
                                               "traversed_from": node_id, "traversed_to": neighbor,
                                               "relation": edge["relation"], "receipt_id": edge["receipt_id"], "edge_id": edge.get("edge_id")}]
                    following.append((neighbor, new_path, new_evidence))
                    if record is not None and record.kind == "SourceRegion":
                        hits.append({"region_id": neighbor, "vector_score": None,
                                     "graph_score": 1 / len(new_path), "graph_paths": [new_path],
                                     "graph_edges": new_evidence})
            frontier = following
            if not frontier:
                break
        selected_ids = {hit["region_id"] for hit in hits}
        # Exact count without scanning all IDs; bounded omission sampling.
        # FAISS wrapper maintains an ID lookup; cache a set once for graph additions.
        # Membership is held alongside the cached index, never reconstructed warm.
        if not hasattr(index, "_nima_ids"):
            index._nima_ids = set(ids)
        indexed_selected = len(selected_ids & index._nima_ids)
        omitted_count = len(ids) - indexed_selected
        omitted = []
        for region_id in ids:
            if len(omitted) >= min(max_omitted, omitted_count):
                break
            if region_id not in selected_ids:
                omitted.append(region_id)
        if store.revision != store_revision:
            raise ConflictError("graph changed during retrieval")
        traversal = {"visited_nodes": len(visited), "examined_edges": traversed,
                     "budget_exhausted": truncated, "hop_limit_reached": bool(frontier),
                     "max_nodes": max_nodes, "max_edges": max_edges,
                     "max_neighbors": max_neighbors, "max_results": max_results}
        projection = EmbeddingProjectionManifest(
            projection_id=identity(index_receipt["manifest"]),
            corpus_id=corpus_id,
            project_id=project_id,
            source_revision=str(index_receipt["manifest"]["generation"]),
            provider=manifest.provider,
            model=manifest.model,
            model_revision=manifest.revision,
            dimension=manifest.dimension,
            indexed_artifact_ids=tuple(ids),
        )
        packet = RetrievalContextPacket(
            query=question,
            corpus_id=corpus_id,
            project_id=project_id,
            graph_revision=graph_revision,
            projection=projection,
            seeds=tuple(
                RetrievalSeed(
                    record_id=hit["region_id"],
                    score=float(hit.get("vector_score") if hit.get("vector_score") is not None else hit.get("graph_score", 0.0)),
                    source="vector" if hit.get("vector_score") is not None else "graph",
                )
                for hit in hits
            ),
            selected_record_ids=tuple(hit["region_id"] for hit in hits),
            selected_graph_refs=tuple(graph_nodes[key].ref for key in sorted(visited) if key in graph_nodes),
            traversal=traversal,
            diagnostics=({"code": "retrieval.truncated", "message": "Retrieval was bounded or omitted results."},)
            if omitted_count > 0 or truncated else (),
            truncated=omitted_count > 0 or truncated,
        )
        selection = Record(kind="SelectionRun", project_id=project_id, corpus_id=corpus_id, content={
            "question": question, "selected": hits, "omitted": omitted, "omitted_count": omitted_count,
            "omitted_complete": len(omitted) == omitted_count,
            "max_hops": max_hops, "graph_revision": graph_revision.model_dump(mode="json"),
            "embedding_manifest": manifest.model_dump(mode="json"), "index_manifest": index_receipt,
            "mode": "hybrid", "vector_branch": "completed", "graph_branch": "completed",
            "truncated": omitted_count > 0 or truncated,
            "traversal": traversal,
            "context_packet": packet.model_dump(mode="json"),
        })
        return [{"id": hit["region_id"], **store.get(hit["region_id"], corpus_id=corpus_id, project_id=project_id).content}
                for hit in hits], selection
