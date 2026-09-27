"""Validate public artifact inputs before copying or packaging any bytes."""
from pathlib import Path
import subprocess
import tempfile

PRIVATE_ROOTS = {'.nima', '.git', '.agents', '.codex', 'article', 'benchmarks', 'graphify-out'}
PRIVATE_NAMES = {'CLEAN_HOST_TEST.md', 'AGENTS.md', 'CODE_TODO.md', 'Milestone.md', 'IMPLEMENTATION_PLAN.md',
                 'TOOL_BENCH.md', 'TOOL_BLUEPRINT.md', 'TOOL_BLUEPRINT.html',
                 'DEFERRED_WORK.json', 'release-fixtures.json', 'SAD.md', 'TODO.md',
                 'LANGFLOW_COMPONENT_AUDIT.md'}


def validate_files(root, names):
    """Reject private, ignored, absent and symlinked inputs, including tracked ones.

    A temporary Git directory gives .gitignore its native semantics even when
    building from a source archive with no Git history or worktree metadata.
    Git is required only to build/stage releases, not to install a wheel.
    """
    root = Path(root).resolve()
    names = sorted(set(names))
    for name in names:
        p = Path(name)
        if p.is_absolute() or '..' in p.parts or not p.parts or p.as_posix() != name:
            raise ValueError(f'Invalid release path: {name}')
        if p.parts[0] in PRIVATE_ROOTS or p.name in PRIVATE_NAMES:
            raise ValueError(f'Private release path: {name}')
        source = root / p
        if any(parent.is_symlink() for parent in [source, *source.parents] if parent != root and root in parent.parents):
            raise ValueError(f'Symlinked release path: {name}')
        if not source.is_file():
            raise ValueError(f'Missing release input: {name}')
        if p.suffix.lower() == '.pdf' or source.read_bytes()[:5] == b'%PDF-':
            raise ValueError(f'PDF is not a release resource: {name}')
    with tempfile.TemporaryDirectory(prefix='nima-release-policy-') as temp:
        git_dir = str(Path(temp) / 'git')
        git = ['git', '-c', 'core.excludesFile=/dev/null', '--git-dir', git_dir, '--work-tree', str(root)]
        subprocess.run([*git, 'init', '-q'], check=True, capture_output=True)
        result = subprocess.run([*git, 'check-ignore', '--no-index', '-z', '--stdin'],
            input=('\0'.join(names) + '\0').encode(), capture_output=True)
        if result.returncode not in (0, 1):
            raise RuntimeError('Could not evaluate release ignore rules: ' + result.stderr.decode())
        ignored = result.stdout.decode().strip('\0').split('\0') if result.stdout else []
        if ignored:
            raise ValueError('Ignored release inputs: ' + ', '.join(ignored))
    return names
