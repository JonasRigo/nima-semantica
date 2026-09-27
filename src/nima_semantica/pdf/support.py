"""Local hashing and error primitives for the independently packaged PDF worker."""
import hashlib
import json
from ..models import NimaError


class PdfError(NimaError):
    def __init__(self, message, *, details=None):
        super().__init__(message)
        self.details = details or {}


def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def sha256_bytes(value):
    return hashlib.sha256(value).hexdigest()


def sha256_file(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()
