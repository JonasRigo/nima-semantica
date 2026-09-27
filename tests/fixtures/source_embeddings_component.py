"""TEST ONLY: deterministic synthetic vectors, never shipped in a user canvas."""
from lfx.custom import Component
from lfx.io import Output
from langchain_core.embeddings import Embeddings, DeterministicFakeEmbedding


class OfflineEmbeddingFixture(Component):
    name = "OfflineEmbeddingFixture"
    display_name = "TEST ONLY synthetic embeddings"
    inputs = []
    outputs = [Output(name="embeddings", display_name="Embeddings", method="embedding")]

    def embedding(self) -> Embeddings:
        return DeterministicFakeEmbedding(size=8)
