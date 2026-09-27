"""Immutable artifact publication and corpus-registry binding."""

from __future__ import annotations

import base64
import csv
import hashlib
import html
import io
from typing import Any, Literal

from .artifact_contracts import ArtifactEnvelope, GraphArtifact
from .corpus_registry import CorpusRegistry
from .execution_receipts import ExecutionReceiptService
from .models import ConflictError, NimaError, Record, StrictModel, canonical, identity
from .receipts import ExecutionReceipt
from .registry_contracts import RegistryEntry, RegistryResourceKind


class ArtifactListItem(StrictModel):
    artifact_id: str
    envelope: ArtifactEnvelope
    bytes: int


class ArtifactListResult(StrictModel):
    artifacts: tuple[ArtifactListItem, ...] = ()
    total: int
    offset: int
    has_more: bool


class ArtifactReadResult(StrictModel):
    artifact_id: str
    encoding: Literal["utf-8", "base64"]
    bytes: int
    content: str


class ArtifactRenderResult(StrictModel):
    record_id: str
    result_artifact_id: str
    artifacts: dict[str, dict[str, Any]]
    origin: Literal["caller_authored_report"] = "caller_authored_report"
    authority: Literal["artifact_write"] = "artifact_write"


class ArtifactService:
    """Store, resolve, list, render, and receipt immutable artifacts."""

    ENVELOPE_KIND = "ArtifactEnvelope"
    REPORT_KIND = "ReportExport"

    def __init__(
        self,
        store,
        registry: CorpusRegistry | None = None,
        *,
        receipts: ExecutionReceiptService | None = None,
    ):
        self.store = store
        self.registry = registry or CorpusRegistry(store)
        self.receipts = receipts or ExecutionReceiptService(store)

    def publish(self, data: bytes, envelope: ArtifactEnvelope, *, registry_revision: str) -> ArtifactEnvelope:
        """Store bytes and bind one validated envelope to a registry revision."""
        if not isinstance(data, bytes):
            raise TypeError("artifact data must be bytes")
        digest = hashlib.sha256(data).hexdigest()
        if envelope.artifact_id != digest or envelope.content_hash != digest:
            raise ConflictError("artifact envelope hashes do not match immutable bytes")

        for source_artifact_id in envelope.source_artifact_ids:
            self.store.read_artifact(source_artifact_id)

        # The filesystem artifact is content-addressed and independently verifies
        # existing bytes. SQLite metadata is committed as one transaction.
        stored_id = self.store.artifact(data)
        if stored_id != envelope.artifact_id:
            raise NimaError("artifact store returned a different content identity")
        existing_entry = self.registry.resolve(
            envelope.artifact_id,
            corpus_id=envelope.corpus_id,
            project_id=envelope.project_id,
            exact_scope=True,
        )
        if existing_entry is not None:
            existing = self.resolve(
                envelope.artifact_id,
                corpus_id=envelope.corpus_id,
                project_id=envelope.project_id,
            )
            if existing != envelope or existing_entry.registry_revision != registry_revision:
                raise ConflictError("artifact ID is already bound to different publication metadata")
            return existing

        with self.store.joined_transaction():
            existing_records = [
                record for _, record in self.store.records(self.ENVELOPE_KIND, corpus_id=envelope.corpus_id)
                if record.project_id == envelope.project_id
                and record.content.get("artifact_id") == envelope.artifact_id
            ]
            if existing_records:
                existing = ArtifactEnvelope.model_validate(existing_records[0].content)
                if existing != envelope:
                    raise ConflictError("artifact ID is already bound to different envelope metadata")
                return existing
            envelope_record = Record(
                kind=self.ENVELOPE_KIND,
                corpus_id=envelope.corpus_id,
                project_id=envelope.project_id,
                content=envelope.model_dump(mode="json"),
            )
            envelope_record_id = self.store.put(envelope_record)
            entry = RegistryEntry(
                resource_id=envelope.artifact_id,
                resource_kind=RegistryResourceKind.ARTIFACT,
                corpus_id=envelope.corpus_id,
                project_id=envelope.project_id,
                content_hash=envelope.content_hash,
                source_revision=envelope.source_revision,
                registry_revision=registry_revision,
                artifact_id=envelope.artifact_id,
                status=("available" if envelope.status == "available" else envelope.status),
                parents=envelope.source_artifact_ids,
                provenance=envelope.provenance,
                metadata={"envelope_record_id": envelope_record_id},
            )
            self.registry.register(entry)
        self._record_publication_receipt(envelope, registry_revision)
        return envelope

    def list(
        self,
        *,
        corpus_id: str,
        project_id: str | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> ArtifactListResult:
        """List registered envelopes in deterministic ID order."""
        if offset < 0 or not 1 <= limit <= 1_000:
            raise ValueError("invalid artifact listing bounds")
        entries = [
            entry for entry in self.registry.list(corpus_id=corpus_id, project_id=project_id)
            if entry.resource_kind is RegistryResourceKind.ARTIFACT
        ]
        entries.sort(key=lambda entry: entry.artifact_id or entry.resource_id)
        selected = entries[offset:offset + limit]
        artifacts = []
        for entry in selected:
            artifact_id = entry.artifact_id or entry.resource_id
            envelope = self.resolve(artifact_id, corpus_id=corpus_id, project_id=project_id)
            artifacts.append(ArtifactListItem(
                artifact_id=artifact_id,
                envelope=envelope,
                bytes=len(self.store.read_artifact(artifact_id)),
            ))
        return ArtifactListResult(
            artifacts=tuple(artifacts),
            total=len(entries),
            offset=offset,
            has_more=offset + len(artifacts) < len(entries),
        )

    def resolve(self, artifact_id: str, *, corpus_id: str, project_id: str | None = None) -> ArtifactEnvelope:
        """Resolve and integrity-check the registered envelope for an artifact."""
        entry = self.registry.resolve(artifact_id, corpus_id=corpus_id, project_id=project_id)
        if entry is None or entry.resource_kind is not RegistryResourceKind.ARTIFACT:
            raise NimaError("artifact is not registered in the requested scope")
        if (entry.artifact_id != artifact_id or entry.resource_id != artifact_id
            or entry.corpus_id != corpus_id or entry.project_id not in (None, project_id)):
            raise NimaError("registry artifact identity mismatch")
        revision = self.registry.revision(entry.registry_revision, corpus_id=corpus_id)
        if revision is None or revision.corpus_id != corpus_id or artifact_id not in revision.changed_resource_ids:
            raise NimaError("artifact registry revision binding is invalid")
        record_id = entry.metadata.get("envelope_record_id")
        if not isinstance(record_id, str):
            raise NimaError("registry entry has no artifact envelope binding")
        record = self.store.get(record_id, corpus_id=corpus_id, project_id=project_id)
        if record is None or record.kind != self.ENVELOPE_KIND or record.corpus_id != corpus_id or record.project_id != entry.project_id:
            raise NimaError("artifact envelope record is unavailable")
        envelope = ArtifactEnvelope.model_validate(record.content)
        if (envelope.corpus_id != corpus_id or envelope.project_id != entry.project_id
            or envelope.artifact_id != artifact_id or envelope.content_hash != entry.content_hash
            or envelope.source_revision != entry.source_revision or envelope.status != entry.status
            or envelope.source_artifact_ids != entry.parents or envelope.provenance != entry.provenance):
            raise NimaError("artifact envelope and registry binding disagree")
        data = self.store.read_artifact(artifact_id)
        digest = hashlib.sha256(data).hexdigest()
        if envelope.artifact_id != digest or envelope.content_hash != digest:
            raise NimaError("artifact envelope integrity check failed")
        return envelope

    def read(self, artifact_id: str, *, corpus_id: str, project_id: str | None = None) -> tuple[ArtifactEnvelope, bytes]:
        envelope = self.resolve(artifact_id, corpus_id=corpus_id, project_id=project_id)
        return envelope, self.store.read_artifact(artifact_id)

    def read_encoded(
        self,
        artifact_id: str,
        *,
        corpus_id: str,
        project_id: str | None = None,
        encoding: Literal["utf-8", "base64"] = "utf-8",
        max_bytes: int = 5_000_000,
    ) -> ArtifactReadResult:
        """Read an authorized artifact with an explicit bounded encoding."""
        if max_bytes < 1 or max_bytes > 5_000_000:
            raise ValueError("invalid artifact read bound")
        _, data = self.read(artifact_id, corpus_id=corpus_id, project_id=project_id)
        if len(data) > max_bytes:
            raise NimaError("artifact exceeds read byte limit")
        try:
            content = data.decode("utf-8") if encoding == "utf-8" else base64.b64encode(data).decode("ascii")
        except UnicodeDecodeError:
            raise NimaError("artifact is not UTF-8; select base64") from None
        return ArtifactReadResult(
            artifact_id=artifact_id, encoding=encoding, bytes=len(data), content=content
        )

    def render(
        self,
        result: dict[str, Any],
        *,
        title: str,
        corpus_id: str,
        project_id: str | None = None,
        registry_revision: str,
        source_record_ids: tuple[str, ...] = (),
        formats: tuple[Literal["html", "markdown", "csv"], ...] = ("html", "markdown", "csv"),
    ) -> ArtifactRenderResult:
        """Persist a caller-authored result and safe deterministic projections."""
        if not title.strip() or not formats or len(set(formats)) != len(formats):
            raise ValueError("render requires a nonempty title and unique formats")
        if len(canonical(result)) > 5_000_000 or len(result) > 1_000:
            raise ValueError("rendered result exceeds bounds")
        for record_id in source_record_ids:
            if self.store.get(record_id, corpus_id=corpus_id, project_id=project_id) is None:
                raise NimaError("source record absent from requested corpus")

        result_bytes = canonical(result)
        result_id = hashlib.sha256(result_bytes).hexdigest()
        format_data = {
            format_name: self._render_bytes(title, result, format_name)
            for format_name in formats
        }
        all_ids = (result_id, *(hashlib.sha256(data).hexdigest() for data in format_data.values()))
        registry = self.registry.revision(registry_revision, corpus_id=corpus_id)
        if registry is None or not set(all_ids) <= set(registry.changed_resource_ids):
            raise ConflictError("registry revision does not declare all rendered artifacts")

        existing = [
            record for _, record in self.store.records(self.REPORT_KIND, corpus_id=corpus_id)
            if record.project_id == project_id and record.content.get("result_artifact_id") == result_id
        ]
        if existing:
            if existing[0].content.get("title") != title or set(existing[0].content.get("artifacts", {})) != set(formats):
                raise ConflictError("rendered result ID is already bound to different report metadata")
            return ArtifactRenderResult(
                record_id=existing[0].id,
                result_artifact_id=result_id,
                artifacts=existing[0].content["artifacts"],
            )

        self.publish(result_bytes, ArtifactEnvelope(
            artifact_id=result_id,
            artifact_kind="report_result",
            media_type="application/json",
            content_hash=result_id,
            corpus_id=corpus_id,
            project_id=project_id,
            source_artifact_ids=tuple(),
            content={"title": title, "origin": "caller_authored_report"},
        ), registry_revision=registry_revision)
        artifacts = {}
        for format_name, data in format_data.items():
            artifact_id = hashlib.sha256(data).hexdigest()
            media_type = {"html": "text/html", "markdown": "text/markdown", "csv": "text/csv"}[format_name]
            self.publish(data, ArtifactEnvelope(
                artifact_id=artifact_id,
                artifact_kind=f"report_{format_name}",
                media_type=media_type,
                content_hash=artifact_id,
                corpus_id=corpus_id,
                project_id=project_id,
                source_artifact_ids=(result_id,),
                provenance=tuple(source_record_ids),
                content={"title": title, "format": format_name, "origin": "caller_authored_report"},
                status="proposed",
            ), registry_revision=registry_revision)
            artifacts[format_name] = {"artifact_id": artifact_id, "media_type": media_type, "bytes": len(data)}

        record = Record(
            kind=self.REPORT_KIND,
            corpus_id=corpus_id,
            project_id=project_id,
            parents=tuple(sorted(set(source_record_ids))),
            content={
                "result_artifact_id": result_id,
                "artifacts": artifacts,
                "title": title,
                "status": "proposed",
                "origin": "caller_authored_report",
            },
        )
        with self.store.transaction():
            record_id = self.store.put(record)
        return ArtifactRenderResult(record_id=record_id, result_artifact_id=result_id, artifacts=artifacts)

    @staticmethod
    def _render_bytes(title: str, result: dict[str, Any], format_name: str) -> bytes:
        rows = [("Authorship", "Caller-authored result; rendering does not establish verification.")]
        rows.extend((key, value if isinstance(value, str) else canonical(value).decode())
                    for key, value in sorted(result.items()))
        if format_name == "html":
            row = lambda values, tag: "<tr>" + "".join(
                f"<{tag}>{html.escape(value)}</{tag}>" for value in values
            ) + "</tr>"
            text = (
                "<!doctype html><html><head><meta charset=\"utf-8\"><title>"
                + html.escape(title) + "</title></head><body><h1>" + html.escape(title)
                + "</h1><table><thead>" + row(("Field", "Value"), "th")
                + "</thead><tbody>" + "".join(row(item, "td") for item in rows)
                + "</tbody></table></body></html>"
            )
            return text.encode("utf-8")
        if format_name == "markdown":
            def escape(value):
                return "".join(c if c.isalnum() or c == " " else f"&#{ord(c)};" for c in value)
            row = lambda values: "| " + " | ".join(escape(value) for value in values) + " |"
            return ("# " + escape(title) + "\n\n" + row(("Field", "Value")) + "\n"
                    + row(("---", "---")) + "\n" + "\n".join(row(item) for item in rows) + "\n").encode("utf-8")
        stream = io.StringIO(newline="")
        writer = csv.writer(stream, quoting=csv.QUOTE_ALL)
        for values in (("Field", "Value"), *rows):
            writer.writerow([
                "'" + value if value.lstrip().startswith(("=", "+", "-", "@"))
                or value.startswith(("\t", "\r", "\n")) else value
                for value in values
            ])
        return stream.getvalue().encode("utf-8")

    def _record_publication_receipt(self, envelope: ArtifactEnvelope, registry_revision: str) -> None:
        operation_id = f"artifact-publish:{envelope.artifact_id}:{registry_revision}"
        receipt_id = identity({"stage": "artifact_publication", "operation_id": operation_id})
        if self.receipts.get(receipt_id, corpus_id=envelope.corpus_id, project_id=envelope.project_id):
            return
        self.receipts.record(ExecutionReceipt(
            receipt_id=receipt_id,
            operation_id=operation_id,
            stage="artifact_publication",
            corpus_id=envelope.corpus_id,
            project_id=envelope.project_id,
            input_ids=envelope.source_artifact_ids,
            output_ids=(envelope.artifact_id,),
            status="completed",
            tool_version="artifact-service-v1",
            metadata={"artifact_kind": envelope.artifact_kind, "registry_revision": registry_revision},
        ))


__all__ = [
    "ArtifactListItem", "ArtifactListResult", "ArtifactReadResult", "ArtifactRenderResult",
    "ArtifactService",
]
