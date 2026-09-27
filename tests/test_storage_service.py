import pytest

from nima_semantica.models import ConflictError, NimaError, Record
from nima_semantica.storage import GraphStore


def test_semantica_nested_roundtrip_and_rollback(tmp_path):
    store = GraphStore(tmp_path)
    record = Record(kind="Claim", content={"nested": {"array": [1, None, True]}})
    with store.transaction():
        store.put(record)
    revision = store.revision
    with pytest.raises(RuntimeError):
        with store.transaction():
            store.put(Record(kind="Failed", content={}))
            raise RuntimeError("failure injection")
    assert store.revision == revision
    assert not store.records("Failed")
    store.close()
    recovered = GraphStore(tmp_path)
    assert recovered.records() == [(record.id, record)]
    recovered.close()


def test_integrity_and_path_confinement(store):
    artifact = store.artifact(b"evidence")
    assert store.read_artifact(artifact) == b"evidence"
    with pytest.raises(NimaError):
        store.read_artifact("../../token")
    (store.root / "artifacts" / artifact).write_bytes(b"corrupted")
    with pytest.raises(NimaError):
        store.read_artifact(artifact)


def test_single_writer(store):
    with pytest.raises(ConflictError):
        GraphStore(store.root)
