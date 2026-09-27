"""Import and export for the file-oriented Open Knowledge Format (OKF).

OKF is the interchange format; the graph contracts in :mod:`okf_contracts`
remain NIMA's internal, revision-aware representation.  This module handles
the OKF v0.2 bundle boundary: Markdown concept files with YAML frontmatter.
"""

from __future__ import annotations

import os
import stat
import uuid
import shutil
import ctypes
from pathlib import Path, PurePosixPath
from typing import Any

import yaml
from pydantic import Field, field_validator, model_validator

from .models import StrictModel


def _concept_id(value: str) -> str:
    """Validate an OKF concept ID, which is a relative path without ``.md``."""
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or value != path.as_posix()
        or "\x00" in value
        or "\\" in value
        or any(part in {"", ".", ".."} for part in path.parts)
        or path.name in {"index", "log"}
        or path.suffix
    ):
        raise ValueError("concept_id must be a relative path without a suffix")
    return value


class OKFConceptDocument(StrictModel):
    """One OKF concept document, independent of its on-disk location."""

    concept_id: str = Field(min_length=1, max_length=512)
    type: str = Field(min_length=1, max_length=128)
    title: str | None = Field(default=None, max_length=512)
    description: str | None = Field(default=None, max_length=4_000)
    resource: str | None = Field(default=None, max_length=2_048)
    tags: tuple[str, ...] = Field(default=(), max_length=128)
    body: str = ""
    extensions: dict[str, Any] = Field(default_factory=dict, max_length=128)

    _validate_concept_id = field_validator("concept_id")(_concept_id)

    @field_validator("tags")
    @classmethod
    def unique_tags(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("tags must be unique")
        return value

    def frontmatter(self) -> dict[str, Any]:
        """Return OKF frontmatter while preserving producer extensions."""
        fields: dict[str, Any] = {"type": self.type}
        for key, value in (
            ("title", self.title),
            ("description", self.description),
            ("resource", self.resource),
            ("tags", list(self.tags) or None),
        ):
            if value is not None:
                fields[key] = value
        overlap = {"type", "title", "description", "resource", "tags"} & set(self.extensions)
        if overlap:
            raise ValueError(f"extensions shadow reserved OKF fields: {sorted(overlap)}")
        fields.update(self.extensions)
        return fields

    def to_markdown(self) -> str:
        frontmatter = yaml.safe_dump(
            self.frontmatter(),
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=True,
        )
        body = self.body.rstrip("\n")
        return f"---\n{frontmatter}---\n{body}\n"

    @classmethod
    def from_markdown(cls, concept_id: str, content: str) -> "OKFConceptDocument":
        lines = content.splitlines()
        if len(lines) < 3 or lines[0].strip() != "---":
            raise ValueError("OKF concept is missing opening YAML frontmatter")
        try:
            closing = lines.index("---", 1)
        except ValueError as exc:
            raise ValueError("OKF concept is missing closing YAML frontmatter") from exc
        raw = yaml.safe_load("\n".join(lines[1:closing]))
        if not isinstance(raw, dict):
            raise ValueError("OKF frontmatter must be a YAML mapping")
        if "type" not in raw:
            raise ValueError("OKF frontmatter requires type")
        known = {"type", "title", "description", "resource", "tags"}
        values = {key: raw.pop(key) for key in list(raw) if key in known}
        values["concept_id"] = concept_id
        values["extensions"] = raw
        values["body"] = "\n".join(lines[closing + 1 :]).lstrip("\n")
        return cls.model_validate(values)


class OKFBundle(StrictModel):
    """A collection of OKF concepts forming one portable knowledge bundle."""

    index_metadata: dict[str, Any] = Field(default_factory=dict)
    index_body: str = ""
    concepts: tuple[OKFConceptDocument, ...] = Field(default=(), max_length=1_000_000)

    @model_validator(mode="after")
    def unique_concept_ids(self) -> "OKFBundle":
        ids = [concept.concept_id for concept in self.concepts]
        if len(ids) != len(set(ids)):
            raise ValueError("OKF bundle contains duplicate concept IDs")
        return self

    def files(self) -> dict[str, str]:
        """Return deterministic relative filenames and Markdown contents."""
        files = {
            f"{concept.concept_id}.md": concept.to_markdown()
            for concept in sorted(self.concepts, key=lambda item: item.concept_id)
        }
        if self.index_metadata or self.index_body:
            files["index.md"] = "---\n" + yaml.safe_dump(self.index_metadata, sort_keys=True, allow_unicode=True) + "---\n" + self.index_body
        return files


def _open_directory(path):
    """Open every path component without following symlinks."""
    path = Path(path)
    if ".." in path.parts:
        raise ValueError("directory traversal is forbidden")
    fd = os.open("/" if path.is_absolute() else ".", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts:
            if part in {"/", "."}:
                continue
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def export_bundle(bundle: OKFBundle, directory: str | Path) -> None:
    """Publish into a new directory; never overwrite an existing destination."""
    bundle = OKFBundle.model_validate(bundle.model_dump())
    root = Path(directory)
    if root.name in {"", ".", ".."}:
        raise ValueError("export requires a new named directory")
    parent = _open_directory(root.parent)
    staging = ".okf-" + uuid.uuid4().hex
    stage_fd = None
    published = False
    try:
        try:
            os.stat(root.name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise FileExistsError("bundle destination already exists")
        os.mkdir(staging, mode=0o700, dir_fd=parent)
        stage_fd = os.open(staging, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        for relative, content in bundle.files().items():
            parts = PurePosixPath(relative).parts
            fd = os.dup(stage_fd)
            try:
                for part in parts[:-1]:
                    try:
                        os.mkdir(part, mode=0o700, dir_fd=fd)
                    except FileExistsError:
                        pass
                    child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                    os.close(fd)
                    fd = child
                target = os.open(parts[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
                with os.fdopen(target, "w", encoding="utf-8") as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.fsync(fd)
            finally:
                os.close(fd)
        os.fsync(stage_fd)
        # Linux renameat2(RENAME_NOREPLACE) makes publication atomic and race-safe.
        libc = ctypes.CDLL(None, use_errno=True)
        rename = libc.renameat2
        rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        if rename(parent, os.fsencode(staging), parent, os.fsencode(root.name), 1) != 0:
            code = ctypes.get_errno()
            raise OSError(code, os.strerror(code))
        published = True
        os.fsync(parent)
    finally:
        if stage_fd is not None:
            os.close(stage_fd)
            if not published:
                shutil.rmtree(staging, dir_fd=parent)
        os.close(parent)


def import_bundle(directory: str | Path) -> OKFBundle:
    """Read regular files using pinned directory descriptors; reject all symlinks."""
    root = _open_directory(directory)
    concepts = []
    metadata, index_body = {}, ""
    def visit(fd, prefix=""):
        nonlocal metadata, index_body
        for name in sorted(os.listdir(fd)):
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if stat.S_ISLNK(info.st_mode):
                raise ValueError("bundle contains a symlink")
            if stat.S_ISDIR(info.st_mode):
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                try:
                    visit(child, prefix + name + "/")
                finally:
                    os.close(child)
                continue
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("bundle contains a non-regular file")
            if not name.endswith(".md"):
                continue
            file_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            with os.fdopen(file_fd, "r", encoding="utf-8") as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise ValueError("bundle file changed type")
                content = stream.read()
            if name in {"index.md", "log.md"}:
                if name == "index.md" and not prefix:
                    if content.startswith("---\n"):
                        closing = content.find("\n---\n", 4)
                        if closing < 0:
                            raise ValueError("invalid index frontmatter")
                        metadata = yaml.safe_load(content[4:closing]) or {}
                        index_body = content[closing + 5:]
                    else:
                        index_body = content
                continue
            concepts.append(OKFConceptDocument.from_markdown((prefix + name)[:-3], content))
    try:
        visit(root)
    finally:
        os.close(root)
    return OKFBundle(concepts=tuple(concepts), index_metadata=metadata, index_body=index_body)


__all__ = ["OKFBundle", "OKFConceptDocument", "export_bundle", "import_bundle"]
