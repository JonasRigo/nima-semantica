"""Native macOS smoke check of the documented source-build NIMA handoff."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


def run(*args):
    result = subprocess.run([sys.executable, "-m", "nima_semantica.cli", *args], check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


root = Path(sys.argv[1]).resolve()
root.mkdir(parents=True, exist_ok=True)
workspace = root / "workspace with spaces"
config = root / "installation.json"
config.write_text(json.dumps(dict(data_root=str(root / "data"), llm=dict(model="qualification", credential=""))))
run("--config", str(config), "project", "init", "smoke", "--corpus", "smoke", "--path", str(workspace), "--harness", "codex", "--offline")
result = run("--config", str(config), "project", "graph", "smoke", "--corpus", "smoke", "--path", str(workspace), "--no-private", "--open")
pid = result["studio_pid"]
try:
    time.sleep(12)
    os.kill(pid, 0)
    command = subprocess.check_output(["ps", "-ww", "-p", str(pid), "-o", "command="], text=True).strip()
    assert "open " + result["directory"] in command, command
    # Capture the actual native application and its upstream folder consent UI.
    subprocess.run(["screencapture", "-x", str(root / "studio.png")], check=True)
    result.update(native_platform=sys.platform, executable=os.environ["NIMA_OKF_STUDIO"], process_command=command,
                  qualification="Native app remains running with the exact exported folder argument; screenshot retained for UI inspection.")
    (root / "qualification.json").write_text(json.dumps(result, indent=2) + "\n")
finally:
    os.kill(pid, signal.SIGTERM)
