"""Native-value conversion and operator-owned store access for flow adapters."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import threading

from lfx.schema import Data, DataFrame, Message
from nima_semantica.providers import ModelManifest

_store_lock = threading.RLock()


def native(value):
    if isinstance(value, Message):
        return value.text
    if isinstance(value, DataFrame):
        return value.to_dict(orient="records")
    if isinstance(value, Data):
        return value.data
    return value


def readable(value):
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and isinstance(value.get("text"), str):
        return value["text"]
    return json.dumps(value, ensure_ascii=False, indent=2)


@contextmanager
def configured_store(*, required=True):
    """Never accept storage paths from model-generated requests or flow data."""
    root = os.environ.get("NIMA_STORE_ROOT")
    if not root:
        if required:
            raise ValueError("Set operator-owned NIMA_STORE_ROOT to an existing v2 store")
        yield None
        return
    if not (Path(root) / "graph.sqlite3").is_file():
        raise ValueError("NIMA_STORE_ROOT must identify an existing v2 store")
    from nima_semantica.storage import GraphStore
    with _store_lock:
        store = GraphStore(Path(root))
        try:
            yield store
        finally:
            store.close()


class EmbeddingProvider:
    def __init__(self, embeddings, manifest):
        self.embeddings = embeddings
        self.manifest = ModelManifest.model_validate(manifest)

    def embed_query(self, profile, text):
        return [self.embeddings.embed_query(text)], self.manifest

    def embed(self, profile, texts):
        return self.embeddings.embed_documents(texts), self.manifest
