"""Scoped graph heads and append-only object history over the shared SQLite store."""

import json

from .models import ConflictError, NimaError, canonical, identity
from .okf_contracts import GraphRevision, OKFNode, OKFEdge, OKFSnapshot


GRAPH_SCHEMA = """
CREATE TABLE IF NOT EXISTS graph_heads (
 scope TEXT PRIMARY KEY, corpus_id TEXT NOT NULL, project_id TEXT, version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS graph_objects (
 scope TEXT NOT NULL, kind TEXT NOT NULL, object_id TEXT NOT NULL, payload TEXT NOT NULL,
 PRIMARY KEY(scope,kind,object_id));
CREATE TABLE IF NOT EXISTS graph_versions (
 scope TEXT NOT NULL, version INTEGER NOT NULL, kind TEXT NOT NULL, object_id TEXT NOT NULL, payload TEXT,
 PRIMARY KEY(scope,version,kind,object_id));
CREATE TABLE IF NOT EXISTS graph_commits (
 scope TEXT NOT NULL, version INTEGER NOT NULL, delta_id TEXT NOT NULL, delta_hash TEXT NOT NULL,
 approval TEXT NOT NULL, delta TEXT NOT NULL, admitted_delta TEXT NOT NULL, receipt_id TEXT NOT NULL, result TEXT NOT NULL,
 PRIMARY KEY(scope,version), UNIQUE(scope,delta_id));
"""


def scope_key(corpus_id, project_id=None):
    return identity([corpus_id, project_id])


class GraphPersistence:
    def graph_revision(self, corpus_id, project_id=None):
        with self._mutex:
            def head(project):
                row = self._db.execute("SELECT version FROM graph_heads WHERE scope=?",
                                       (scope_key(corpus_id, project),)).fetchone()
                return row[0] if row else 0
            return GraphRevision(corpus_id=corpus_id, corpus_revision=head(None), project_id=project_id,
                                 project_revision=head(project_id) if project_id is not None else None)

    def get_okf_operation(self, delta_id, *, corpus_id, project_id=None):
        with self._mutex:
            row = self._db.execute(
                "SELECT delta_hash,approval,result FROM graph_commits WHERE scope=? AND delta_id=?",
                (scope_key(corpus_id, project_id), delta_id)).fetchone()
            return None if row is None else dict(delta_hash=row[0], approval=json.loads(row[1]), result=json.loads(row[2]))

    def _graph_objects(self):
        nodes, edges = {}, {}
        for kind, payload in self._db.execute("SELECT kind,payload FROM graph_objects"):
            item = (OKFNode if kind == "node" else OKFEdge).model_validate_json(payload)
            (nodes if kind == "node" else edges)[item.ref] = item
        return nodes, edges

    def prospective_graph(self, delta):
        nodes, edges = self._graph_objects()
        for ref in delta.remove_edge_ids:
            if ref not in edges:
                raise ConflictError("removed edge is absent in its exact scope")
            del edges[ref]
        for ref in delta.remove_node_ids:
            if ref not in nodes:
                raise ConflictError("removed node is absent in its exact scope")
            del nodes[ref]
        nodes.update((node.ref, node) for node in delta.upsert_nodes)
        edges.update((edge.ref, edge) for edge in delta.add_edges)
        for node in nodes.values():
            if any(parent not in nodes for parent in node.parents):
                raise ConflictError("prospective graph contains a missing parent")
        for edge in edges.values():
            if edge.source_id not in nodes or edge.target_id not in nodes:
                raise ConflictError("prospective graph contains a dangling edge")
        return nodes, edges

    def persist_graph_commit(self, delta, approval, receipt_id, result, approved_delta):
        if not self._in_transaction:
            raise NimaError("graph commit requires a transaction")
        scope = scope_key(delta.corpus_id, delta.project_id)
        version = (delta.base_revision.corpus_revision if delta.project_id is None
                   else delta.base_revision.project_revision) + 1
        self._db.execute("INSERT INTO graph_heads VALUES (?,?,?,?) ON CONFLICT(scope) DO UPDATE SET version=excluded.version",
                         (scope, delta.corpus_id, delta.project_id, version))
        for kind, items, removed in (("node", delta.upsert_nodes, delta.remove_node_ids),
                                      ("edge", delta.add_edges, delta.remove_edge_ids)):
            for item in items:
                payload = canonical(item).decode()
                self._db.execute("INSERT INTO graph_objects VALUES (?,?,?,?) ON CONFLICT(scope,kind,object_id) DO UPDATE SET payload=excluded.payload",
                                 (scope, kind, item.ref.local_id, payload))
                self._db.execute("INSERT INTO graph_versions VALUES (?,?,?,?,?)", (scope, version, kind, item.ref.local_id, payload))
            for ref in removed:
                self._db.execute("DELETE FROM graph_objects WHERE scope=? AND kind=? AND object_id=?", (scope, kind, ref.local_id))
                self._db.execute("INSERT INTO graph_versions VALUES (?,?,?,?,NULL)", (scope, version, kind, ref.local_id))
        if identity(approved_delta) != approval.delta_hash:
            raise ConflictError("approved delta hash differs from retained audit payload")
        self._db.execute("INSERT INTO graph_commits VALUES (?,?,?,?,?,?,?,?,?)",
                         (scope, version, delta.delta_id, approval.delta_hash, canonical(approval).decode(),
                          canonical(approved_delta).decode(), canonical(delta).decode(), receipt_id, canonical(result).decode()))

    def read_okf_snapshot(self, *, corpus_id, project_id=None, revision=None):
        with self._mutex:
            head = self.graph_revision(corpus_id, project_id)
            revision = head if revision is None else GraphRevision.model_validate(revision)
            if (revision.corpus_id, revision.project_id) != (corpus_id, project_id):
                raise ConflictError("graph revision scope mismatch")
            if revision.corpus_revision > head.corpus_revision or (revision.project_revision or 0) > (head.project_revision or 0):
                raise ConflictError("unknown graph revision")
            selected = [(None, revision.corpus_revision)]
            if project_id is not None:
                selected.append((project_id, revision.project_revision))
            nodes, edges = [], []
            for project, version in selected:
                rows = self._db.execute("""SELECT v.kind,v.payload FROM graph_versions v
                    WHERE v.scope=? AND v.version<=? AND v.version=(
                    SELECT MAX(w.version) FROM graph_versions w WHERE w.scope=v.scope
                    AND w.kind=v.kind AND w.object_id=v.object_id AND w.version<=?)""",
                    (scope_key(corpus_id, project), version, version)).fetchall()
                for kind, payload in rows:
                    if payload is not None:
                        (nodes if kind == "node" else edges).append((OKFNode if kind == "node" else OKFEdge).model_validate_json(payload))
            nodes.sort(key=lambda n: canonical(n.ref))
            edges.sort(key=lambda e: canonical(e.ref))
            profiles = {n.ontology_profile for n in nodes}
            snapshot = OKFSnapshot(snapshot_id=identity(revision), graph_revision=revision,
                                   corpus_id=corpus_id, project_id=project_id, nodes=tuple(nodes), edges=tuple(edges),
                                   ontology_profile=next(iter(profiles)) if len(profiles) == 1 else None)
            from .evidence_contracts import validate_snapshot_evidence
            if not validate_snapshot_evidence(self, snapshot).valid:
                raise NimaError("snapshot contains invalid evidence")
            return snapshot
