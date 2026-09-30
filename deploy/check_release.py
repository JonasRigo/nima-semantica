"""Check source-archive completeness and wheel bytes against the staged manifest."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import tarfile
import zipfile

from release_policy import PRIVATE_NAMES, PRIVATE_ROOTS


def public_name(name):
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts or not path.parts:
        raise ValueError(f'Unsafe archive path: {name}')
    if set(path.parts) & PRIVATE_ROOTS or path.name in PRIVATE_NAMES or path.suffix.lower() == '.pdf':
        raise ValueError(f'Private archive path: {name}')


def check(source_archive, wheel):
    source_archive, wheel = Path(source_archive), Path(wheel)
    with tarfile.open(source_archive) as source:
        members = source.getmembers()
        roots = {PurePosixPath(item.name).parts[0] for item in members}
        if len(roots) != 1:
            raise ValueError('Source archive must have one root')
        prefix = roots.pop() + '/'
        files = {}
        for item in members:
            public_name(item.name)
            if not (item.isfile() or item.isdir()):
                raise ValueError(f'Nonregular archive entry: {item.name}')
            if item.isfile():
                name = item.name.removeprefix(prefix)
                if name in files:
                    raise ValueError(f'Duplicate source entry: {name}')
                files[name] = source.extractfile(item).read()
    manifest = json.loads(files['RELEASE_MANIFEST.json'])
    for name, digest in manifest['files'].items():
        public_name(name)
        if name not in files or hashlib.sha256(files[name]).hexdigest() != digest:
            raise ValueError(f'Source manifest mismatch: {name}')
    version = manifest['version']
    inventory = json.loads(files['release-resources.json'])['files']
    with zipfile.ZipFile(wheel) as binary:
        names = binary.namelist()
        if len(names) != len(set(names)):
            raise ValueError('Duplicate wheel entries')
        for name in names:
            public_name(name)
        metadata = binary.read(f'nima_semantica-{version}.dist-info/METADATA').decode()
        if f'\nVersion: {version}\n' not in metadata:
            raise ValueError('Wheel version mismatch')
        for name in inventory:
            if binary.read(f'nima_semantica-{version}.data/data/share/nima/{name}') != files[name]:
                raise ValueError(f'Wheel resource mismatch: {name}')
        code = {name.removeprefix('src/'): name for name in manifest['files']
                if name.startswith('src/nima_semantica/')}
        shipped = {name for name in names if name.startswith('nima_semantica/') and not name.endswith('/')}
        if shipped != set(code):
            raise ValueError('Wheel implementation inventory mismatch')
        for name, original in code.items():
            if binary.read(name) != files[original]:
                raise ValueError(f'Wheel code mismatch: {name}')
    return {'version': version, 'source_files': len(manifest['files']),
            'resources': len(inventory), 'implementation_files': len(code),
            'sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (source_archive, wheel)}}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source_archive')
    parser.add_argument('wheel')
    args = parser.parse_args()
    print(json.dumps(check(args.source_archive, args.wheel), indent=2))
