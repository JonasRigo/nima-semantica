from nima_semantica.artifact_service import ArtifactService
from nima_semantica.corpus_registry import CorpusRegistry
from nima_semantica.registry_contracts import CorpusDescriptor
from nima_semantica.source_tools import PrepareSourcesRequest, SourceToolContext, prepare_sources, embed_sources, project_sources
from nima_semantica.storage import GraphStore


def test_same_bytes_are_separately_bound_and_private_regions_do_not_leak(tmp_path):
    store = GraphStore(tmp_path / "store")
    CorpusRegistry(store).register_corpus(CorpusDescriptor(corpus_id="papers", name="Papers", corpus_revision="1"))
    outputs = {}
    for project in ("a", "b", None):
        ctx = SourceToolContext(corpus_id="papers", project_id=project, source_scope="project" if project else "corpus", allow_project_writes=bool(project), allow_corpus_writes=not project)
        req = PrepareSourcesRequest(mode="prepare_index", operation_id="ingest-" + str(project), sources=[{"name": "notes.txt", "text": "My exact private notes about integer addition."}])
        prepared = prepare_sources(store, req, ctx)
        assert prepared.status == "complete", prepared
        result = project_sources(store, embed_sources(store, prepared, ctx), ctx)
        assert result.status == "complete", result
        outputs[project] = prepared.data
    assert len({v["region_ids"][0] for v in outputs.values()}) == 3
    region = outputs["a"]["region_ids"][0]
    from nima_semantica.source_corpus import SourceCorpusService
    assert SourceCorpusService(store).get_region(region, corpus_id="papers", project_id="a")
    assert SourceCorpusService(store).get_region(region, corpus_id="papers", project_id="b") is None
    assert SourceCorpusService(store).get_region(region, corpus_id="papers") is None
    digest = outputs["a"]["sources"][0]["original_artifact_id"]
    assert ArtifactService(store).resolve(digest, corpus_id="papers", project_id="a").project_id == "a"
    assert ArtifactService(store).resolve(digest, corpus_id="papers").project_id is None
    store.close()
