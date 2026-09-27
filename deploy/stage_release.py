"""Stage the public source inventory without private data or development history."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import shutil
from release_policy import validate_files

ROOT = Path(__file__).resolve().parents[1]


def stage(destination):
    destination = Path(destination).resolve()
    if destination.exists():
        raise ValueError('Release destination must not already exist')
    files = set(json.loads((ROOT / 'release-resources.json').read_text())['files'])
    files.update({'README.md', 'CITATION.cff', '.zenodo.json', '.gitignore', '.dockerignore',
                  'pyproject.toml', 'setup.py', 'MANIFEST.in', 'Dockerfile.langflow',
                  'constraints-tested.txt', '.github/workflows/tests.yml'})
    for folder, patterns in (('src', ('*.py', '*.json')), ('tests', ('*',))):
        for pattern in patterns:
            files.update(p.relative_to(ROOT).as_posix() for p in (ROOT / folder).rglob(pattern)
                         if p.is_file() and not {'__pycache__', '.pytest_cache'} & set(p.parts)
                         and not any(part.endswith('.egg-info') for part in p.parts))
    names = validate_files(ROOT, files)
    manifest = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in names}
    destination.mkdir(parents=True)
    for name in names:
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, target)
    (destination / 'RELEASE_MANIFEST.json').write_text(json.dumps({'version': '0.1.0', 'files': manifest}, indent=2) + '\n')
    return {'destination': str(destination), 'files': len(manifest),
            'bytes': sum((destination / name).stat().st_size for name in names)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination')
    print(json.dumps(stage(parser.parse_args().destination), indent=2))
