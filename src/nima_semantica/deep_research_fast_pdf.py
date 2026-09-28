"""Bounded text-layer PDF extraction subprocess for provisional reviews."""

from io import BytesIO
import resource
import sys


def extract_macos(data):
    """Use a Linux memory limit; Darwin cannot enforce RLIMIT_AS."""
    import subprocess
    import uuid
    from . import __version__
    if len(data) > 12_000_000 or not data.startswith(b"%PDF-"):
        raise ValueError("invalid or oversized PDF input")
    name = "nima-fast-pdf-" + uuid.uuid4().hex
    command = ["docker", "run", "--name", name, "--pull=never", "--network=none",
               "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges:true",
               "--pids-limit=16", "--memory=1g", "--memory-swap=1g", "--cpus=1",
               "--user=65534:65534", "--tmpfs=/tmp:rw,noexec,nosuid,size=16m", "-i",
               f"nima-fast-pdf:{__version__}"]
    try:
        result = subprocess.run(command, input=data, capture_output=True, timeout=50, check=False)
        if result.returncode:
            raise ValueError("fast PDF extraction failed; run nima setup --provision and check Docker")
        if len(result.stdout) > 4_000_000:
            raise ValueError("fast PDF extracted text exceeds output limit")
        return result.stdout
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError("isolated fast PDF extraction unavailable") from exc
    finally:
        try:
            cleanup = subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=15)
        except OSError:
            # Missing Docker cannot have created a container.
            if __import__("shutil").which("docker"):
                raise
        else:
            if cleanup.returncode and b"No such container" not in cleanup.stderr:
                raise ValueError("fast PDF container cleanup failed; operator intervention required")


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
