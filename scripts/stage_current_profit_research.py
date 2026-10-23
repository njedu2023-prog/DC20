"""Stage the current research dataset without copying accumulated snapshots.

Historical versions remain in Git. Public latest.json and its hash-addressed
dataset are copied byte-for-byte; source files are never deleted or rewritten.
"""
import hashlib
import json
from pathlib import Path
import re
import shutil


def stage(source, destination):
    source, destination = Path(source), Path(destination)
    if source.is_symlink() or not source.is_dir() or destination.exists():
        raise ValueError('UNSAFE_RESEARCH_STAGING_ROOT')
    pointer_path = source / 'latest.json'
    if pointer_path.is_symlink():
        raise ValueError('UNSAFE_RESEARCH_POINTER')
    pointer = json.loads(pointer_path.read_bytes())
    relative = pointer.get('path', '')
    if not re.fullmatch(r'versions/[0-9a-f]{64}\.json', relative):
        raise ValueError('UNSAFE_RESEARCH_VERSION_PATH')
    version = source / relative
    if version.is_symlink() or version.parent.is_symlink():
        raise ValueError('UNSAFE_RESEARCH_VERSION')
    digest = hashlib.sha256(version.read_bytes()).hexdigest()
    if pointer.get('sha256') != digest or relative != f'versions/{digest}.json':
        raise ValueError('RESEARCH_VERSION_HASH_MISMATCH')
    # Validate all copied paths before creating the destination.
    for child in source.rglob('*'):
        if child.is_symlink():
            raise ValueError('UNSAFE_RESEARCH_SYMLINK')
    destination.mkdir(parents=True)
    for child in source.iterdir():
        if child.name == 'versions':
            continue
        target = destination / child.name
        if child.is_dir():
            shutil.copytree(child, target)
        else:
            shutil.copy2(child, target)
    (destination / 'versions').mkdir()
    shutil.copy2(version, destination / relative)


if __name__ == '__main__':
    import sys
    stage(*sys.argv[1:])
