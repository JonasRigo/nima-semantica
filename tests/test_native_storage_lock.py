"""Corpus ownership must be enforced across native CLI/Langflow processes."""
import subprocess
import sys

from nima_semantica.storage import GraphStore


def test_native_process_cannot_bypass_writer_lease_and_can_reopen(tmp_path):
    store = GraphStore(tmp_path)
    script = """
import sys
from pathlib import Path
from nima_semantica.storage import GraphStore
from nima_semantica.models import ConflictError
try:
    store = GraphStore(Path(sys.argv[1]))
except ConflictError:
    print('locked')
else:
    print('opened')
    store.close()
"""
    def probe():
        return subprocess.check_output([sys.executable, "-c", script, str(tmp_path)], text=True, timeout=30).strip()
    try:
        assert probe() == "locked"
    finally:
        store.close()
    assert probe() == "opened"
