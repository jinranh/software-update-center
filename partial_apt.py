#!/usr/bin/python3
"""Read and install from a fresh signed APT snapshot when a repository fails."""
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile

import apt
import apt_pkg
import backend
import official_apt as vendor

INSTALL = ['--no-remove', '--no-install-recommends', '--only-upgrade']
INDEX = re.compile(r'_Packages(?:\.(?:lz4|xz|gz|zst))?$')
SNAPSHOT = re.compile(r'partial-[a-z0-9_]{8}')


def settings(lists):
    return {'Dir::State::lists': lists, 'Dir::Cache::pkgcache': '', 'Dir::Cache::srcpkgcache': ''}


def refresh_lists(lists):
    # A fresh directory has no previous indexes that APT could silently reuse.
    lists.mkdir(mode=0o755)
    try:
        backend.run([*vendor.APT_BASE, *vendor.apt_options(settings(lists)),
                     '-o', 'APT::Update::Error-Mode=any', 'update'], timeout=145)
        return ''
    except RuntimeError as error:
        if not any(path.is_file() and INDEX.search(path.name) for path in lists.iterdir()):
            raise
        return str(error)


def weak_origins():
    must_be_false = {'trusted', 'allow-insecure', 'allow-weak', 'allow-downgrade-to-insecure'}
    must_be_true = {'check-date', 'check-valid-until'}
    freshness_overrides = {'valid-until-min', 'date-max-future'}
    origins = set()
    for path in vendor.source_paths():
        for source in vendor.source_entries(path):
            if not source.enabled:
                continue
            for key, value in source.fields.items():
                value = value.strip().casefold()
                if ((key in must_be_false and apt_pkg.string_to_bool(value) != 0)
                        or (key in must_be_true and apt_pkg.string_to_bool(value) != 1)
                        or (key in freshness_overrides and value != '0')):
                    origins.update(uri.rstrip('/') for uri in source.uris)
    return origins


def snapshot_path(name):
    if not isinstance(name, str) or not SNAPSHOT.fullmatch(name):
        raise RuntimeError('部分软件源检查结果无效，请重新检查。')
    return backend.CACHE / name


def collect_snapshot(root):
    lists = root / 'lists'
    if not lists.is_dir():
        raise RuntimeError('部分软件源检查结果已过期，请重新检查。')
    weak = weak_origins()
    with vendor.cache_settings(settings(lists)):
        cache = apt.Cache(memonly=True)
        return backend.collect_cache(cache, accept=lambda version: any(
            trusted and not vendor.weakened_origin(origin, weak) and bool(re.fullmatch(r'[0-9a-f]{64}', digest))
            for _, digest, trusted, origin, _ in vendor.package_archives(cache, version)))


def save_snapshot():
    root = Path(tempfile.mkdtemp(prefix='partial-', dir=backend.CACHE))
    try:
        notice = refresh_lists(root / 'lists')
        return root.name, collect_snapshot(root), notice
    except Exception:
        shutil.rmtree(root)
        raise


class Review(vendor.Session):
    app = None  # This route excludes all catalog packages.

    def __init__(self, scratch, lists):
        self.scratch = scratch
        self.archives = scratch / 'archives'
        self.archives.mkdir(mode=0o755)
        self.settings = {**settings(lists), 'Dir::Cache::archives': self.archives}
        self.weakened = weak_origins()

    def plan(self, spec):
        backend.validate_specs([spec])
        backend.validate_update_route([spec])
        name, target = spec.split('=', 1)
        with vendor.cache_settings(self.settings):
            cache = apt.Cache(memonly=True)
            if name not in cache:
                raise RuntimeError('本次已确认的软件源未提供所选版本。')
            package = cache[name]
            if (not package.is_installed or not package.candidate
                    or package.candidate.version != target
                    or apt_pkg.version_compare(target, package.installed.version) <= 0):
                raise RuntimeError('软件版本或候选来源已变化，请重新检查。')
            if package._pkg.selected_state == apt_pkg.SELSTATE_HOLD:
                raise RuntimeError('软件版本已锁定。')
            command = [*self.commands(), *INSTALL, 'install', spec]
            simulation = backend.run([*command, '--simulate'])
            backend.validate_update_plan(simulation)
            changes = vendor.changes_from_simulation(simulation)
            if not any(item[0].split(':')[0] == name.split(':')[0] and item[2] == target for item in changes):
                raise RuntimeError('APT 计划没有更新所选应用。')
            return {'spec': spec, 'archives': self.review_archives(cache, command, changes, backend.PROTECTED)}


def preview(spec):
    with (backend.CACHE / 'check.lock').open('rb') as lock:
        fcntl.flock(lock, fcntl.LOCK_SH)
        state = json.loads((backend.CACHE / 'state.json').read_text())
        root = snapshot_path(state.get('partial_snapshot'))
        with vendor.workdir() as scratch:
            plan = Review(scratch, root / 'lists').plan(spec)
    return plan


def apply(spec, expected, runner):
    if os.geteuid() != 0:
        raise RuntimeError('需要本机管理员授权。')
    if not re.fullmatch(r'[0-9a-f]{64}', expected):
        raise ValueError('无效的部分更新确认记录。')
    backend.validate_specs([spec])
    backend.validate_update_route([spec])
    vendor.LOCK.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    with vendor.LOCK.open('a') as lock, vendor.workdir() as scratch:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        lists = scratch / 'lists'
        refresh_lists(lists)
        session = Review(scratch, lists)
        plan = session.plan(spec)
        if vendor.plan_digest(plan) != expected:
            raise RuntimeError('APT 候选包、下载来源或依赖已变化。请返回窗口重新审查。')
        command = [*session.commands(), *INSTALL, 'install', spec]
        vendor.apply_reviewed(session, plan, runner, command, lambda: session.plan(spec))
        name, version = spec.split('=', 1)
        if vendor.local_version(name) != version:
            raise RuntimeError('dpkg 未报告所选版本已安装。')
        print(f'已核对安装版本：{version}', flush=True)


if __name__ == '__main__':
    try:
        from install import apt_run
        apply(*sys.argv[1:], runner=apt_run)
    except (Exception, KeyboardInterrupt) as error:
        message = str(error) or '操作已取消。'
        print(' '.join(message.splitlines())[-2400:], file=sys.stderr)
        sys.exit(1)
