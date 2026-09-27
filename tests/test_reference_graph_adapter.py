from nima_semantica.flow_adapters import input_context, normalize_output


def test_authenticated_conversion_is_proposal_only(store):
    from conftest import seed_region
    region = seed_region(store, "The claim follows from the stated premise.")
    _, ctx = input_context("source", corpus_id="papers", project_id="p", request_id="r",
        graph_revision=store.graph_revision("papers", "p"), ontology_profile="claim_obligation",
        source_region_ids=[region.id], chunk_bindings={"chunk1": region.id})
    raw = {"objects": [{"components": [{"component_id": "c1", "statement": "A claim",
            "source_chunk_ids": ["chunk1"], "derivation_level": "detailed"}],
            "obligations": [], "relations": [], "scope_complete": True}]}
    before = store.graph_revision("papers", "p")
    result = normalize_output(raw, ctx, preset="graph_extraction", store=store)
    assert result.normalized.status == "complete"
    delta = result.normalized.graph_delta
    assert delta is not None
    assert delta.upsert_nodes[0].status == "proposed"
    assert delta.upsert_nodes[0].evidence[0].region_id == region.id
    assert store.graph_revision("papers", "p") == before
    raw["objects"][0]["components"][0]["source_chunk_ids"] = ["fabricated"]
    rejected = normalize_output(raw, ctx, preset="graph_extraction", store=store)
    assert rejected.normalized.status == "partial"
    assert rejected.normalized.graph_delta is None
    assert "unbound source chunk" in rejected.normalized.diagnostics[0].message


def test_relations_need_explicit_evidence_and_foreign_regions_fail(store):
    from conftest import seed_region
    region = seed_region(store, project_id="private")
    _, ctx = input_context("source", corpus_id="papers", project_id="p", request_id="r",
        graph_revision=store.graph_revision("papers", "p"), ontology_profile="claim_obligation",
        source_region_ids=[region.id], chunk_bindings={"chunk1": region.id})
    raw = {"components": [{"component_id": "c1", "source_chunk_ids": ["chunk1"]}]}
    result = normalize_output(raw, ctx, preset="graph_extraction", store=store)
    assert result.normalized.graph_delta is None
    assert "evidence validation failed" in result.normalized.diagnostics[0].message
