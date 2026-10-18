"""Upgrade known managed skills while preserving independently edited copies."""
import hashlib,json
from pathlib import Path
from .models import canonical

def managed_skill_plan(root,binding,harnesses,source_root):
    root=Path(root).resolve();source_root=Path(source_root)
    manifest=root/'.nima/skills.json';known=json.loads(manifest.read_text()).get('files',{}) if manifest.exists() else {}
    baseline=None
    executable=(binding.get('cli_command') or [None])[0]
    if executable:
        candidate=Path(executable).parent.parent/'share/nima/skills'
        if candidate.is_dir():baseline=candidate
    destinations=[root/'.agents/skills']
    if 'claude' in harnesses:destinations.append(root/'.claude/skills')
    from .installation import assets
    legacy_file=assets()/'references/managed-skill-baselines.json'
    historical=json.loads(legacy_file.read_text()).get('files',{}) if legacy_file.exists() else {}
    writes={};managed=dict(known);conflicts=[]
    for destination in destinations:
        for source in sorted(source_root.rglob('*')):
            if not source.is_file() or '__pycache__' in source.parts:continue
            relative=source.relative_to(source_root);target=destination/relative;key=target.relative_to(root).as_posix()
            if any(p.is_symlink() for p in (target,*target.parents) if p==root or root in p.parents):raise ValueError('Refusing symlink installed skill')
            data=source.read_bytes();new=hashlib.sha256(data).hexdigest()
            old=hashlib.sha256(target.read_bytes()).hexdigest() if target.exists() else None
            legacy=baseline/relative if baseline else None
            expected=known.get(key) or (hashlib.sha256(legacy.read_bytes()).hexdigest() if legacy and legacy.is_file() else None)
            if old not in (None,new,expected) and old not in historical.get(relative.as_posix(),[]):
                conflicts.append(key);continue
            writes[target]=data;managed[key]=new
    writes[manifest]=canonical(dict(schema_version=1,files=managed)) + b'\n'
    return writes,conflicts
