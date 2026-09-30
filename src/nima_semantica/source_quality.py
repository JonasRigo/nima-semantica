"""Preparation quality is independent of visibility and scientific status."""

PROVISIONAL = "fast_provisional"
WARNING = "Fast extraction: not fully validated; equations, layout and citations may be incomplete."


def preparation_quality(region):
    metadata = region.metadata if hasattr(region, "metadata") else region.get("metadata", {})
    return metadata.get("preparation_quality", "full")


def evidence_locator(region):
    value = region.model_dump() if hasattr(region, "model_dump") else region
    locator = {key: value[key] for key in ("start", "end", "ordinal")}
    if preparation_quality(region) == PROVISIONAL:
        locator.update(preparation_quality=PROVISIONAL, warning=WARNING)
    for key in ("extraction_method", "acquisition_id", "provenance_artifact_id"):
        if key in value.get("metadata", {}):
            locator[key] = value["metadata"][key]
    return locator


def provisional_object(item):
    return item.properties.get("preparation_quality") == PROVISIONAL or any(
        ref.locator.get("preparation_quality") == PROVISIONAL for ref in item.evidence)


def provisional_nodes(nodes):
    """Propagate preparation warnings along explicit derivation parents."""
    values = {node.ref: node for node in nodes}
    affected = {ref for ref, node in values.items() if provisional_object(node)}
    while True:
        more = {ref for ref, node in values.items() if any(parent in affected for parent in node.parents)}
        if more <= affected:
            return affected
        affected.update(more)
