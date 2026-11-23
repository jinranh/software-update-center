"""Identify and preserve the dedicated legacy Chrome source during repair."""
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re


@dataclass(frozen=True)
class Repair:
    legacy: Path
    canonical: Path
    backup: Path
    legacy_hash: str
    canonical_hash: str


def discover(paths, directory, repo, arch, parse, configured_source):
    legacy = directory / 'google-chrome.list'
    canonical = directory / repo.source_file
    for path in (legacy, canonical):
        if path not in paths or path.is_symlink():
            raise RuntimeError(f'需要独立的 Chrome 软件源文件：{path}。')
        entries = list(parse(path))
        if len(entries) != 1 or entries[0].types != ['deb'] or not entries[0].enabled:
            raise RuntimeError(f'{path} 必须包含单一条目，其他配置需要手动核对。')
        if path == legacy:
            entry = entries[0]
            if (len(entry.uris) != 1 or entry.uris[0].rstrip('/') not in repo.aliases
                    or entry.suites != [repo.suite] or entry.components != [repo.component]
                    or entry.fields.keys() - {'arch', 'signed-by'}
                    or entry.fields.get('arch', arch) != arch
                    or (entry.key and not re.fullmatch(r'/[^\s,]+\.(asc|gpg)', entry.key))):
                raise RuntimeError(f'{legacy} 不是受支持的旧版 Chrome 条目，请手动核对。')
    retained = configured_source(repo, [path for path in paths if path != legacy], arch)
    if retained is None or retained.path != canonical:
        raise RuntimeError(f'需要保留带 Signed-By 的官方 Chrome 软件源：{canonical}。')
    backup = legacy.with_name(legacy.name + '.software-update-center.disabled')
    if backup.exists() or backup.is_symlink():
        raise RuntimeError(f'备份已存在：{backup}。请先核对原文件和这份备份。')
    return Repair(legacy, canonical, backup, file_digest(legacy), file_digest(canonical))


def file_digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def preserve_legacy(repair):
    # link() refuses an existing backup and preserves the original inode and metadata.
    os.link(repair.legacy, repair.backup, follow_symlinks=False)
    if (repair.backup.is_symlink() or not repair.backup.samefile(repair.legacy)
            or file_digest(repair.backup) != repair.legacy_hash):
        raise RuntimeError(f'旧版 Chrome 软件源已变化，请核对：{repair.legacy}。')
    repair.legacy.unlink()
