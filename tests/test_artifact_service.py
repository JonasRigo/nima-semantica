import base64
import hashlib

from nima_semantica.artifact_contracts import ArtifactEnvelope
from nima_semantica.artifact_service import ArtifactService
from nima_semantica.corpus_registry import CorpusRegistry
from nima_semantica.models import Record
from nima_semantica.registry_contracts import CorpusDescriptor, RegistryRevision


def registry(store, changed):
    value = CorpusRegistry(store)
    value.register_corpus(CorpusDescriptor(corpus_id="papers", name="Papers", corpus_revision="corpus:1"))
    value.register_revision(RegistryRevision(
        revision_id="registry:1", corpus_id="papers", sequence=1,
        changed_resource_ids=tuple(changed),
    ))
    return value


def test_artifact_service_lists_and_reads_registered_bytes(tmp_path):
    store = __import__("nima_semantica.storage", fromlist=["GraphStore"]).GraphStore(tmp_path)
    try:
        data = b"artifact bytes"
        digest = hashlib.sha256(data).hexdigest()
        service = ArtifactService(store, registry(store, (digest,)))
        service.publish(data, ArtifactEnvelope(
            artifact_id=digest,
            artifact_kind="source_text",
            media_type="text/plain",
            content_hash=digest,
            corpus_id="papers",
        ), registry_revision="registry:1")

        listing = service.list(corpus_id="papers")
        encoded = service.read_encoded(digest, corpus_id="papers", encoding="base64")

        assert listing.total == 1 and listing.artifacts[0].artifact_id == digest
        assert base64.b64decode(encoded.content) == data
    finally:
        store.close()


def test_artifact_service_renders_safe_proposal_artifacts(tmp_path):
    store = __import__("nima_semantica.storage", fromlist=["GraphStore"]).GraphStore(tmp_path)
    try:
        result = {"formula": "=UNSAFE()", "status": "verified"}
        title = "Report <title>"
        result_id = hashlib.sha256(__import__("nima_semantica.models", fromlist=["canonical"]).canonical(result)).hexdigest()
        formats = {name: ArtifactService._render_bytes(title, result, name) for name in ("html", "markdown", "csv")}
        changed = [result_id, *(hashlib.sha256(data).hexdigest() for data in formats.values())]
        service = ArtifactService(store, registry(store, changed))
        source = Record(kind="Evidence", corpus_id="papers", content={"value": "source"})
        with store.transaction():
            source_id = store.put(source)

        rendered = service.render(
            result,
            title=title,
            corpus_id="papers",
            registry_revision="registry:1",
            source_record_ids=(source_id,),
        )

        assert rendered.origin == "caller_authored_report"
        assert "<script>" not in service.read_encoded(
            rendered.artifacts["html"]["artifact_id"], corpus_id="papers"
        ).content
        assert service.render(
            result,
            title=title,
            corpus_id="papers",
            registry_revision="registry:1",
            source_record_ids=(source_id,),
        ) == rendered
    finally:
        store.close()
