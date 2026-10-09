"""Immutable local report versions and a checked, atomic publication pointer."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import time
from uuid import uuid4

RELEASE_PATTERN = re.compile(r'report_[0-9a-f]{16}\Z')
REQUIRED = {'report.html', 'report_data.json', 'analysis_contract.json'}


def digest(value):
    return hashlib.sha256(value).hexdigest()


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False).encode('utf-8')


def stamp():
    return datetime.now(timezone.utc).isoformat()


def safe_file(folder, name):
    path = PurePosixPath(name)
    if (not name or path.is_absolute() or path.as_posix() != name
            or any(part in ('..', '.') or ':' in part or '\\' in part for part in path.parts)):
        raise ValueError('Unsafe report artifact path')
    target = folder.joinpath(*path.parts)
    if not target.resolve().is_relative_to(folder.resolve()) or target.is_symlink():
        raise ValueError('Report artifact escapes its version directory')
    return target


def version_folder(root, release):
    if not isinstance(release, str) or not RELEASE_PATTERN.fullmatch(release):
        raise ValueError('Invalid report release identifier')
    folder = Path(root) / 'versions' / release
    if folder.is_symlink() or not folder.resolve().is_relative_to(Path(root).resolve()):
        raise ValueError('Report version escapes the delivery directory')
    return folder


def write_new(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as stream:
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())


def create_version(root, artifacts, metadata):
    release = reserve_version(root)
    return complete_version(root, release, artifacts, metadata)


def reserve_version(root):
    release = 'report_' + uuid4().hex[:16]
    version_folder(root, release).mkdir(parents=True)
    return release


def complete_version(root, release, artifacts, metadata):
    if not REQUIRED <= artifacts.keys() or 'manifest.json' in artifacts:
        raise ValueError('Incomplete report version')
    folder = version_folder(root, release)
    if not folder.is_dir() or (folder/'manifest.json').exists():
        raise ValueError('Report version is not an unfinished reservation')
    for name, value in artifacts.items():
        target = safe_file(folder, name)
        if target.exists():
            if target.read_bytes() != value:
                raise ValueError('Reserved report input changed before completion')
        else:
            write_new(target, value)
    manifest = {'format_version': 1, 'release': release, 'created_at_utc': stamp(),
                'files': {name: digest(value) for name, value in artifacts.items()}, 'metadata': metadata}
    # A directory without this last file is never a complete, activatable version.
    write_new(folder / 'manifest.json', encode(manifest))
    verify_version(root, release)
    return release


def verify_version(root, release, expected_manifest=None):
    folder = version_folder(root, release)
    raw = (folder / 'manifest.json').read_bytes()
    if expected_manifest is not None and digest(raw) != expected_manifest:
        raise ValueError('Report manifest no longer matches the publication record')
    manifest = json.loads(raw)
    if type(manifest.get('format_version')) is not int or manifest['format_version'] != 1 or manifest.get('release') != release:
        raise ValueError('Invalid report version manifest')
    files = manifest.get('files', {})
    if not isinstance(files, dict) or not REQUIRED <= files.keys():
        raise ValueError('Incomplete report version manifest')
    for name, expected in files.items():
        if digest(safe_file(folder, name).read_bytes()) != expected:
            raise ValueError('Report artifact integrity check failed: ' + name)
    actual = {p.relative_to(folder).as_posix() for p in folder.rglob('*') if p.is_file()}
    if actual != set(files) | {'manifest.json'}:
        raise ValueError('Unexpected files in report version')
    return {'release': release, 'manifest_sha256': digest(raw), 'manifest': manifest,
            'report': str(folder / 'report.html')}


def current(root):
    path = safe_file(Path(root), 'current.json')
    if not path.exists():
        return None
    pointer = json.loads(path.read_bytes())
    if type(pointer.get('format_version')) is not int or pointer['format_version'] != 1 or not re.fullmatch(r'[0-9a-f]{32}', pointer.get('publication', '')):
        raise ValueError('Invalid report publication pointer')
    record = safe_file(Path(root), 'history/' + pointer['publication'] + '.json')
    if json.loads(record.read_bytes()) != pointer:
        raise ValueError('Report pointer does not match its publication record')
    version = verify_version(root, pointer['release'], pointer['manifest_sha256'])
    return {**pointer, 'report': version['report']}


@contextmanager
def publication_lock(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    # Persistent file, OS lock: process exit releases the lock without deleting old data.
    with safe_file(root, 'publication.lock').open('a+b') as stream:
        if stream.seek(0, os.SEEK_END) == 0:
            stream.write(b'0'); stream.flush()
        deadline = time.monotonic() + 10
        while True:
            stream.seek(0)
            try:
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError('Another report publication holds the lock')
                time.sleep(0.05)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def activate(root, release, expected_publication):
    root = Path(root)
    with publication_lock(root):
        prior = current(root)
        actual = prior['publication'] if prior else None
        if actual != expected_publication:
            raise ValueError('Current report changed; review it before retrying publication')
        verified = verify_version(root, release)
        pointer = {'format_version': 1, 'publication': uuid4().hex, 'release': release,
                   'manifest_sha256': verified['manifest_sha256'], 'activated_at_utc': stamp(),
                   'previous_release': prior['release'] if prior else None}
        value = encode(pointer)
        write_new(safe_file(root, 'history/' + pointer['publication'] + '.json'), value)
        temporary = safe_file(root, '.next_' + pointer['publication'] + '.json')
        write_new(temporary, value)
        # The old pointer stays valid until the new, complete pointer replaces it.
        os.replace(temporary, safe_file(root, 'current.json'))
        return {**pointer, 'report': verified['report']}
