"""Structural mathematical contracts pinned to an immutable ontology digest.

Exact evidence correspondence and these checks do not establish scientific truth.
"""
from .ontology_profiles import saved_profile

FIELDS = ("exact_statement", "quantifiers", "domain", "parameters", "normalization", "smoothing", "distance", "dimension_dependence", "attribution", "assumptions")
KINDS = {"projectrequirement": "desired", "hypothesis": "hypothesis", "counterexample": "counterexample", "statement": "source_statement"}

def contract_issues(profile, nodes, edges):
    if profile.name != "beyond_iid_mathematics":
        return []
    issues = []
    def fail(message, **location):
        issues.append(dict(code="ontology.mathematics_contract", severity="error", message=message, **location))
    if profile.digest != saved_profile("beyond_iid_mathematics").digest:
        fail("Unsupported mathematical contract digest; install an explicitly reviewed new version.")
        return issues
    for node in nodes:
        loc = dict(node_id=node.node_id)
        p = node.properties
        kind = node.node_type.casefold()
        if kind == "source":
            if not isinstance(p.get("attribution"), str) or not p["attribution"].strip():
                fail("Source requires bibliographic attribution.", **loc)
            continue
        m = p.get("mathematics")
        if not isinstance(m, dict):
            fail("Node requires versioned mathematics properties.", **loc)
            continue
        for field in FIELDS:
            if not isinstance(m.get(field), str) or not m[field].strip():
                fail("Missing exact mathematical field: " + field, **loc)
        if not isinstance(m.get("unresolved"), list) or any(not isinstance(v, str) or not v.strip() for v in m.get("unresolved", [])):
            fail("Mathematics unresolved must be an explicit list of nonempty strings.", **loc)
        if any(m.get(field, "").casefold() == "unknown" for field in FIELDS if isinstance(m.get(field), str)) and not m.get("unresolved"):
            fail("Unknown mathematical fields require an unresolved issue.", **loc)
        if kind in KINDS and m.get("epistemic_kind") != KINDS[kind]:
            fail("Mathematical epistemic kind differs from node type.", **loc)
        # Consolidation may preserve variants, but cannot collapse distinct conventions.
        for variant in p.get("consolidation_variants", []):
            other = variant.get("properties", {}).get("mathematics", {})
            for field in ("domain", "parameters", "normalization", "smoothing", "distance", "dimension_dependence"):
                if other.get(field) != m.get(field):
                    fail("Consolidation combined unequal mathematical conventions: " + field, **loc)
    necessary = {r.name.casefold() for r in profile.relation_types if r.necessary_dependency}
    for edge in edges:
        if edge.relation.casefold() not in necessary:
            continue
        expected = "explicit_condition" if edge.relation.casefold() == "has_assumption" else "proof_specific"
        if edge.properties.get("dependency_scope") != expected or not isinstance(edge.properties.get("rationale"), str) or not edge.properties.get("rationale", "").strip() or not edge.evidence:
            fail("Necessary dependency requires exact evidence, rationale and scope " + expected, edge_id=edge.edge_id)
    # Reject cycles among explicitly necessary prerequisites, without treating citations as edges.
    adjacent = {}
    for edge in edges:
        if edge.relation.casefold() in necessary:
            adjacent.setdefault(edge.source_id, set()).add(edge.target_id)
    done = set()
    for root in tuple(adjacent):
        if root in done: continue
        active = {root}
        stack = [(root, iter(adjacent.get(root, ())))]
        while stack:
            node, children = stack[-1]
            child = next(children, None)
            if child is None:
                active.remove(node); done.add(node); stack.pop()
            elif child in active:
                fail("Explicit necessary prerequisites contain a cycle; represent unresolved circular reasoning separately.")
                return issues
            elif child not in done:
                active.add(child); stack.append((child, iter(adjacent.get(child, ()))))
    return issues
