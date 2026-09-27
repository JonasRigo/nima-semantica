"""Ship the reviewed live resource inventory alongside the Python distribution."""
import json
import runpy
from pathlib import Path
from setuptools import setup

root = Path(__file__).parent
inventory = json.loads((root / "release-resources.json").read_text())
policy = runpy.run_path(str(root / "deploy/release_policy.py"))
inputs = inventory["files"] + [p.relative_to(root).as_posix() for p in (root / "src").rglob("*")
                             if p.is_file() and p.suffix in (".py", ".json") and "__pycache__" not in p.parts
                             and not any(part.endswith(".egg-info") for part in p.parts)]
policy["validate_files"](root, inputs)
files = {}
for name in inventory["files"]:
    relative = Path(name)
    if relative.is_absolute() or ".." in relative.parts or not (root / relative).is_file():
        raise ValueError(f"Invalid or missing release resource: {name}")
    files.setdefault(str(Path("share/nima") / relative.parent), []).append(name)
setup(data_files=[(target, sorted(paths)) for target, paths in sorted(files.items())])
