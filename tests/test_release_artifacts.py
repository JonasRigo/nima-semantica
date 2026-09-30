"""Archive checks reject private files and non-reproducible implementation bytes."""
import importlib.util
import hashlib
import io
import json
from pathlib import Path
import tarfile
import zipfile

import pytest


@pytest.fixture
def checker(monkeypatch):
    root = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(root / 'deploy'))
    spec = importlib.util.spec_from_file_location('release_artifact_checker', root / 'deploy/check_release.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('path', [
    '../secret', '/secret', 'archive/.nima/config.json', 'archive/AGENTS.md',
    'archive/paper.pdf', 'archive/.codex/config.toml', 'archive/graphify-out/graph.json',
])
def test_rejects_private_or_escaping_paths(checker, path):
    with pytest.raises(ValueError):
        checker.public_name(path)


def test_public_package_paths(checker):
    for path in ('archive/.github/workflows/tests.yml', 'nima_semantica/cli.py',
                 'archive/tests/model_fixture.py', 'archive/RELEASE_MANIFEST.json'):
        checker.public_name(path)


def artifacts(tmp_path, *, missing_source=False, tampered_code=False, tampered_resource=False):
    code = b'__version__ = "0.1.2"\n'
    files = {'src/nima_semantica/__init__.py': code, 'README.md': b'Public documentation',
             'release-resources.json': json.dumps({'files': ['README.md']}).encode()}
    manifest = {'version': '0.1.2', 'files': {n: hashlib.sha256(b).hexdigest() for n, b in files.items()}}
    files['RELEASE_MANIFEST.json'] = json.dumps(manifest).encode()
    source = tmp_path / 'source.tar.gz'
    with tarfile.open(source, 'w:gz') as archive:
        for name, body in files.items():
            if missing_source and name == 'README.md':
                continue
            member = tarfile.TarInfo('nima_semantica-0.1.2/' + name)
            member.size = len(body)
            archive.addfile(member, io.BytesIO(body))
    wheel = tmp_path / 'package.whl'
    with zipfile.ZipFile(wheel, 'w') as archive:
        archive.writestr('nima_semantica/__init__.py', b'changed' if tampered_code else code)
        archive.writestr('nima_semantica-0.1.2.data/data/share/nima/README.md',
                         b'changed' if tampered_resource else files['README.md'])
        archive.writestr('nima_semantica-0.1.2.dist-info/METADATA', 'Name: nima-semantica\nVersion: 0.1.2\n')
    return source, wheel


def test_archive_roundtrip(checker, tmp_path):
    source, wheel = artifacts(tmp_path)
    result = checker.check(source, wheel)
    assert result['version'] == '0.1.2' and result['implementation_files'] == 1
    assert result['source_files'] == 3 and result['resources'] == 1
    assert result['sha256'][wheel.name] == hashlib.sha256(wheel.read_bytes()).hexdigest()


@pytest.mark.parametrize('option', ['missing_source', 'tampered_code', 'tampered_resource'])
def test_incomplete_or_modified_artifact_rejected(checker, tmp_path, option):
    with pytest.raises(ValueError, match='mismatch'):
        checker.check(*artifacts(tmp_path, **{option: True}))
