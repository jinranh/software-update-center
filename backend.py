#!/usr/bin/python3
"""Repository-backed desktop applications; never install during checks."""
import configparser
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import apt
import apt_pkg
import catalog

ROOT = Path(__file__).resolve().parent
PREVIEW = os.environ.get('SOFTWARE_UPDATE_CENTER_PREVIEW') == '1'
CACHE = Path.home() / ('.cache/software-update-center-preview' if PREVIEW
                       else '.cache/software-update-center')
EXCLUDED = {'docker-desktop', 'workbuddy', 'mongodb-compass', 'mongodb-compass-community',
            'bytedance-feishu-stable'}
PROTECTED = EXCLUDED | catalog.PACKAGES
ENV = dict(os.environ, LC_ALL='C')


def run(command, timeout=60):
    result = subprocess.run(command, capture_output=True, text=True, errors='replace',
                            env=ENV, timeout=timeout)
    if result.returncode:
        raise RuntimeError((result.stdout + result.stderr)[-6000:])
    return result.stdout


def options():
    return ['-c', str(ROOT / 'check.conf'), '-o', 'Dir::State::lists=' + str(CACHE / 'lists'),
            '-o', 'Dir::Cache::pkgcache=', '-o', 'Dir::Cache::srcpkgcache=']


def refresh():
    (CACHE / 'lists/partial').mkdir(parents=True, exist_ok=True)
    run(['/usr/bin/apt-get', *options(), '-o', 'APT::Update::Error-Mode=any',
         '-o', 'Acquire::Retries=1', '-o', 'Acquire::https::Timeout=20',
         '-o', 'Acquire::http::Timeout=20', 'update'], timeout=300)


def desktop_apps():
    result = run(['/usr/bin/dpkg-query', '-S', '/usr/share/applications/*.desktop'])
    names = {}
    for line in result.splitlines():
        if ': /' not in line:
            continue
        package, path = line.split(': ', 1)
        package = package.split(':')[0]
        try:
            cfg = configparser.ConfigParser(interpolation=None, strict=False)
            cfg.read(path, encoding='utf-8')
            entry = cfg['Desktop Entry']
            if entry.get('NoDisplay', 'false').lower() == 'true' or entry.get('Hidden', 'false').lower() == 'true':
                continue
            name = entry.get('Name[zh_CN]') or entry.get('Name')
            if name:
                names.setdefault(package, name)
        except (OSError, KeyError, configparser.Error):
            continue
    return names


def app_record(package, title):
    if (package.name in EXCLUDED or not package.is_installed or
            package.candidate is None or not package.candidate.downloadable):
        return None
    held = package._pkg.selected_state == 2  # dpkg hold
    old, new = package.installed.version, package.candidate.version
    return dict(package=package.name, name=title, installed=old, candidate=new,
                held=held, available=not held and apt_pkg.version_compare(new, old) > 0)


def collect():
    apt_pkg.config.set('Dir::State::lists', str(CACHE / 'lists'))
    apt_pkg.config.set('Dir::Cache::pkgcache', '')
    apt_pkg.config.set('Dir::Cache::srcpkgcache', '')
    cache = apt.Cache()
    rows = []
    for name, title in desktop_apps().items():
        if name not in catalog.PACKAGES and name in cache:
            record = app_record(cache[name], title)
            if record:
                rows.append(record)
    return sorted(rows, key=lambda row: (not row['available'], row['name'].lower()))


def check():
    CACHE.mkdir(parents=True, exist_ok=True)
    with (CACHE / 'check.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('另一个检查任务正在运行，请稍后重试。')
        try:
            refresh()
            state = dict(checked_at=time.time(), apps=collect())
            temp = CACHE / 'state.tmp'
            temp.write_text(json.dumps(state, ensure_ascii=False))
            temp.replace(CACHE / 'state.json')
            (CACHE / 'last-error.txt').unlink(missing_ok=True)
            return state
        except Exception as error:
            (CACHE / 'last-error.txt').write_text(str(error))
            raise


def validate_specs(specs):
    if not specs or len(specs) != len(set(specs)):
        raise ValueError('请选择需要更新的软件。')
    for value in specs:
        if not re.fullmatch(r'[a-z0-9][a-z0-9+.-]*(?::[a-z0-9]+)?=[A-Za-z0-9.+:~_-]+', value):
            raise ValueError('无效的软件包或版本。')
        if value.split('=', 1)[0].split(':')[0] in EXCLUDED:
            raise ValueError('此软件不在本次更新范围。')
    return specs


def selected_specs(rows, names):
    if not names or len(names) != len(set(names)):
        raise ValueError('请选择需要更新的软件。')
    indexed = {row['package']: row for row in rows}
    specs = []
    for name in names:
        row = indexed.get(name)
        if not row or not row['available']:
            raise ValueError('软件更新状态已变化，请重新检查。')
        specs.append(name + '=' + row['candidate'])
    return validate_specs(specs)


def validate_plan(output):
    if any(line.startswith('Remv ') for line in output.splitlines()):
        raise RuntimeError('本次操作会移除软件，已阻止。')
    changes = [line.split()[1] for line in output.splitlines() if line.startswith('Inst ')]
    if any(name.split(':')[0] in EXCLUDED for name in changes):
        raise RuntimeError('依赖变更涉及暂不处理的软件，已阻止。')
    if not changes:
        raise RuntimeError('没有可安装的更新，请重新检查。')
    return changes


def validate_update_route(specs):
    if any(spec.split('=', 1)[0].split(':')[0] in catalog.PACKAGES for spec in specs):
        raise RuntimeError('请从「官方应用」页核对厂商来源并更新此软件。')


def validate_update_plan(output):
    changes = validate_plan(output)
    if any(name.split(':')[0] in catalog.PACKAGES for name in changes):
        raise RuntimeError('依赖变更涉及目录内软件，请先在「官方应用」页核对来源。')


def preview(specs):
    validate_specs(specs)
    validate_update_route(specs)
    output = run(['/usr/bin/apt-get', *options(), '-s', '--no-remove',
                  '--no-install-recommends', '--only-upgrade', 'install', *specs])
    validate_update_plan(output)
    return output


if __name__ == '__main__':
    try:
        if sys.argv[1:] == ['--check']:
            print(json.dumps(check(), ensure_ascii=False))
        elif sys.argv[1:] == ['--cached']:
            # Keep the time of the last successful repository check. Re-read dpkg
            # state so completed updates disappear even if the network is down.
            with (CACHE / 'check.lock').open('w') as lock:
                fcntl.flock(lock, fcntl.LOCK_SH)
                state = json.loads((CACHE / 'state.json').read_text())
                state['apps'] = collect()
            print(json.dumps(state, ensure_ascii=False))
        elif sys.argv[1:2] == ['--preview']:
            print(preview(sys.argv[2:]))
        else:
            raise ValueError('Unknown operation')
    except Exception as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
