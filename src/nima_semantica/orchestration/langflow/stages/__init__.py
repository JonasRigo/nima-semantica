"""Maintained typed transport adapters only."""
from importlib import import_module

_EXPORTS = {
    "InspectableStage": "base", "body": "base", "result": "base",
    "InputNormalizer": "foundational", "GraphRetrievalNormalizer": "foundational",
    "HypothesisGeneration": "foundational", "HypothesisComparison": "foundational",
    "GraphCommit": "foundational", "GraphContextPacket": "graph_retrieval",
    "ValidateOKFSnapshot": "ontology",
}
def __getattr__(name):
    if name not in _EXPORTS:
        raise AttributeError(name)
    return getattr(import_module("." + _EXPORTS[name], __name__), name)
__all__ = list(_EXPORTS)
