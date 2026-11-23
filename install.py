#!/usr/bin/python3
"""Interactive selected APT upgrades; this script always runs as the user."""
import codecs
from dataclasses import dataclass
import fcntl
import json
import os
import re
import subprocess
import sys
import time
import backend


@dataclass(frozen=True)
class AptRun:
    code: int
    output: str = ''

    @property
    def fetch_failed(self):
        return self.code == 100 and bool(re.search(
            r'^E[:：]\s*(?:Failed to fetch|无法下载|无法获取)\s', self.output, re.M))

    def failure_message(self, fallback):
        if self.fetch_failed:
            if any(word in self.output.lower() for word in ('timed out', '超时')):
                return '下载超时'
            return '安装包下载失败'
        return fallback


def lock_error(code, text):
    return code == 100 and any(word in text for word in (
        'Could not get lock', 'Unable to acquire', 'Unable to lock directory',
        '无法获得锁', '无法获取锁', '无法对目录', '锁正由进程'))


def wait_idle(busy, sleep, attempts=30):
    for _ in range(attempts):
        if not busy():
            return True
        sleep(10)
    return False


def packagekit_busy():
    from gi.repository import Gio, GLib
    bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
    try:
        paths = bus.call_sync('org.freedesktop.PackageKit', '/org/freedesktop/PackageKit',
            'org.freedesktop.PackageKit', 'GetTransactionList', None,
            GLib.VariantType.new('(ao)'), Gio.DBusCallFlags.NONE, 5000, None).unpack()[0]
    except GLib.Error as error:
        if 'ServiceUnknown' in str(error):
            return False
        raise
    if paths:
        print('其他软件管理任务正在运行，10 秒后重查（最多 5 分钟）……', flush=True)
    return bool(paths)


def apt_run(command):
    if not wait_idle(packagekit_busy, time.sleep):
        print('软件管理任务持续占用，请稍后重试。')
        return AptRun(1)
    for attempt in range(3):
        output = []
        # sudo and apt retain this terminal's stdin; never pass a password through code.
        with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              ) as process:
            decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
            for chunk in iter(lambda: os.read(process.stdout.fileno(), 4096), b''):
                text = decoder.decode(chunk)
                print(text, end='', flush=True)
                output.append(text)
            code = process.wait()
        combined = ''.join(output)
        with (backend.CACHE/'install.log').open('a') as log:
            log.write(combined)
        if not lock_error(code, combined) or attempt == 2:
            return AptRun(code, combined)
        print('等待软件包锁释放，10 秒后重试……', flush=True)
        time.sleep(10)


def system_plan(specs):
    # Recheck the system's actual installed/hold state after its repository refresh.
    cache = backend.apt.Cache()
    for spec in specs:
        name, target = spec.split('=', 1)
        if name not in cache or not cache[name].is_installed:
            raise RuntimeError('已安装软件发生变化，请重新检查。')
        package = cache[name]
        if package._pkg.selected_state == 2:
            raise RuntimeError('软件版本已锁定，请重新检查。')
        if backend.apt_pkg.version_compare(target, package.installed.version) <= 0:
            raise RuntimeError('软件已是所选版本或更新版本，请重新检查。')
    protected = [name+'='+cache[name].installed.version for name in sorted(backend.EXCLUDED)
                 if name in cache and cache[name].is_installed]
    output = backend.run(['/usr/bin/apt-get', '-s', '--no-remove', '--no-install-recommends',
                          '--only-upgrade', 'install', *specs, *protected])
    backend.validate_plan(output)
    return protected


def target_installed(spec):
    name, target = spec.split('=', 1)
    query = subprocess.run(['/usr/bin/dpkg-query', '-W', '-f',
                            '${Status}\t${Version}', name],
                           capture_output=True, text=True)
    status, _, version = query.stdout.partition('\t')
    return (query.returncode == 0 and status.endswith(' ok installed')
            and backend.apt_pkg.version_compare(version, target) >= 0)


def upgrade(specs, runner=apt_run, planner=system_plan, installed=target_installed):
    backend.validate_specs(specs)
    names = {spec: spec.split('=', 1)[0] for spec in specs}
    base = ['/usr/bin/sudo', '/usr/bin/apt-get', '-o', 'DPkg::Lock::Timeout=180',
            '-o', 'Acquire::https::Timeout=25', '-o', 'Acquire::http::Timeout=25',
            '-o', 'Acquire::Retries=1']
    install_command = [*base, '--no-remove', '--no-install-recommends', '--only-upgrade']
    failures = {}

    def result(code, message=''):
        updated = [name for spec, name in names.items() if installed(spec)]
        failed = {name: reason for name, reason in failures.items() if name not in updated}
        pending = [name for name in names.values() if name not in updated and name not in failed]
        return dict(code=code, updated=updated, failures=failed, pending=pending, message=message)

    def fetch(group):
        # A successful download stage caches the signed APT archives without invoking dpkg.
        return runner([*install_command, '--download-only', '--assume-yes',
                       'install', *group, *planner(group)])

    def apply(group):
        # Recheck the system plan after downloading. APT still asks for final approval.
        outcome = runner([*install_command, '--no-download',
                          'install', *group, *planner(group)])
        missing = [names[spec] for spec in group if not installed(spec)]
        if missing:
            reason = '未安装所选版本' if outcome.code == 0 else '安装未完成或已取消'
            failures.update(dict.fromkeys(missing, outcome.failure_message(reason)))
            return outcome.code or 1
        return outcome.code

    refreshed = runner([*base, '-o', 'APT::Update::Error-Mode=any', 'update'])
    if refreshed.code:
        return result(refreshed.code, '软件源刷新未完成。')
    remaining = [spec for spec in specs if not installed(spec)]
    if not remaining:
        return result(0)
    try:
        downloaded = fetch(remaining)
        if downloaded.code == 0:
            return result(apply(remaining))
        reason = downloaded.failure_message('下载未完成或已取消')
        if not downloaded.fetch_failed or len(remaining) == 1:
            failures.update(dict.fromkeys((names[spec] for spec in remaining), reason))
            return result(downloaded.code)

        print('\n批量下载有失败，正在逐项检查。已在缓存中的安装包会继续使用。', flush=True)
        for spec in remaining:
            if installed(spec):
                continue
            name = names[spec]
            print(f'\n正在处理 {name}……', flush=True)
            downloaded = fetch([spec])
            if downloaded.code:
                failures[name] = downloaded.failure_message('下载未完成或已取消')
                if downloaded.fetch_failed:
                    continue
                return result(downloaded.code)
            code = apply([spec])
            if code:
                return result(code, '安装已中止，未处理的应用可稍后重试。')
        summary = result(100)
        if not summary['failures'] and not summary['pending']:
            summary['code'] = 0
        return summary
    except (RuntimeError, KeyboardInterrupt) as error:
        return result(1, str(error) or '操作已中断。')


def main():
    if len(sys.argv) < 3 or not re.fullmatch('[0-9a-f]{32}',sys.argv[1]):
        return 2
    token = sys.argv[1]
    backend.CACHE.mkdir(parents=True, exist_ok=True)
    result = dict(code=1, updated=[], failures={}, pending=[], message='')
    lock = (backend.CACHE/'install.lock').open('w')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if os.geteuid() == 0:
            raise RuntimeError('请以普通用户打开软件更新窗口。')
        print('请在此终端输入本机密码。安装包会先下载，随后请核对 APT 清单并输入 Y。\n', flush=True)
        (backend.CACHE/'install.log').write_text(time.strftime('%Y-%m-%d %H:%M:%S')+'\n')
        result = upgrade(sys.argv[2:])
        for name, reason in result['failures'].items():
            print(f'{name}：{reason}。')
        if result['updated']:
            print('\n已达到目标版本：'+'、'.join(result['updated'])+'。请重新打开相关应用。')
        if result['pending']:
            print('待处理：'+'、'.join(result['pending']))
        if result['message']:
            print(result['message'])
        if result['code']:
            print(f"更新未全部完成（退出码 {result['code']}）。可在软件更新窗口重试。", flush=True)
        if result['updated']:
            title = '部分更新完成' if result['code'] else '更新完成'
            subprocess.run(['/usr/bin/notify-send', '-a', '软件更新', title,
                            '请重新打开已更新的应用以使用新版本。'])
    except BlockingIOError:
        result['message'] = '已有安装任务正在运行，请使用原安装窗口。'
        print(result['message'])
    except (Exception, KeyboardInterrupt) as error:
        result['message'] = str(error) or '操作已中断。'
        print('\n更新未完成：'+result['message'], flush=True)
    finally:
        path = backend.CACHE/('result-'+token+'.json')
        temp = path.with_suffix('.tmp')
        temp.write_text(json.dumps(result))
        temp.replace(path)
        lock.close()
    try:
        input('\n按回车关闭此窗口。')
    except (EOFError, KeyboardInterrupt):
        pass
    return result['code']

if __name__ == '__main__':
    sys.exit(main())
