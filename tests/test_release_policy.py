"""Release privacy must hold independently of local Git tracking state."""
from pathlib import Path
import importlib.util
import subprocess
import pytest

SPEC = importlib.util.spec_from_file_location('release_policy', Path(__file__).resolve().parents[1] / 'deploy/release_policy.py')
policy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(policy)


def test_ignored_tracked_file_is_rejected(tmp_path):
    (tmp_path / '.gitignore').write_text('private/\n')
    (tmp_path / 'private').mkdir()
    (tmp_path / 'private/note.txt').write_text('private note')
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    subprocess.run(['git', '-C', str(tmp_path), 'add', '-f', 'private/note.txt'], check=True)
    with pytest.raises(ValueError, match='Ignored release inputs'):
        policy.validate_files(tmp_path, ['private/note.txt'])


def test_ignore_negation_works_without_git_history(tmp_path):
    (tmp_path / '.gitignore').write_text('references/*\n!references/public.json\n')
    (tmp_path / 'references').mkdir()
    (tmp_path / 'references/public.json').write_text('{}')
    assert policy.validate_files(tmp_path, ['references/public.json']) == ['references/public.json']
    assert not (tmp_path / '.git').exists()


@pytest.mark.parametrize('name', ['benchmarks/report.txt', 'article/draft.tex', 'CODE_TODO.md', 'TOOL_BLUEPRINT.md', 'docs/CLEAN_HOST_TEST.md'])
def test_explicit_private_paths_fail_without_ignore_file(tmp_path, name):
    with pytest.raises(ValueError, match='Private release path'):
        policy.validate_files(tmp_path, [name])


def test_symlinked_directory_is_rejected(tmp_path):
    (tmp_path / 'outside').mkdir()
    (tmp_path / 'outside/file.txt').write_text('hidden')
    (tmp_path / 'public').symlink_to(tmp_path / 'outside', target_is_directory=True)
    with pytest.raises(ValueError, match='Symlinked'):
        policy.validate_files(tmp_path, ['public/file.txt'])
