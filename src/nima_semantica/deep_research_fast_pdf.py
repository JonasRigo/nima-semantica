"""Bounded text-layer PDF extraction subprocess for provisional reviews."""

from io import BytesIO
import resource
import sys


def main():
    resource.setrlimit(resource.RLIMIT_CPU, (35, 35))
    resource.setrlimit(resource.RLIMIT_AS, (1_000_000_000, 1_000_000_000))
    data = sys.stdin.buffer.read(12_000_001)
    if len(data) > 12_000_000 or not data.startswith(b"%PDF-"):
        raise ValueError("invalid or oversized PDF input")
    from pypdf import PdfReader
    reader = PdfReader(BytesIO(data), strict=False)
    if len(reader.pages) > 150:
        raise ValueError("fast PDF page limit exceeded")
    text = "\f".join(page.extract_text() or "" for page in reader.pages)
    output = text.encode("utf-8")
    if len(output) > 4_000_000:
        raise ValueError("fast PDF extracted text exceeds output limit")
    sys.stdout.buffer.write(output)


if __name__ == "__main__":
    main()
