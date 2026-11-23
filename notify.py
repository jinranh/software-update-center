#!/usr/bin/python3
"""Daily check and a clickable notification; never installs software."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import sys
import gi
from gi.repository import Gio, GLib
import backend
import catalog

ROOT = Path(__file__).resolve().parent


def open_updates():
    # The timer's service stops its whole cgroup when the notifier exits.
    subprocess.run(['/usr/bin/systemd-run', '--user', '--collect', '--quiet', '--service-type=exec',
                    '/usr/bin/python3', str(ROOT / 'app.py'), '--updates'],
                   check=True, timeout=10)


def notify(state):
    available = [row for row in state['apps'] if row['available']]
    if not available:
        return
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    loop = GLib.MainLoop()
    notification_id = [None]
    def event(connection, sender, path, interface, signal, parameters, data):
        values = parameters.unpack()
        if values[0] != notification_id[0]:
            return
        if signal == 'ActionInvoked':
            try:
                if values[1] in ('default', 'open'):
                    open_updates()
            except (OSError, subprocess.SubprocessError) as error:
                print(error, file=sys.stderr)
            finally:
                loop.quit()
        elif signal == 'NotificationClosed':
            loop.quit()
    subscription = bus.signal_subscribe('org.freedesktop.Notifications',
        'org.freedesktop.Notifications', None, '/org/freedesktop/Notifications',
        None, Gio.DBusSignalFlags.NONE, event, None)
    names = '、'.join(row['name'] for row in available[:4])
    text = names + (' 等' if len(available)>4 else '') + '\n点击查看并选择更新，不会自动安装。'
    result = bus.call_sync('org.freedesktop.Notifications', '/org/freedesktop/Notifications',
        'org.freedesktop.Notifications', 'Notify',
        GLib.Variant('(susssasa{sv}i)', ('软件更新', 0, 'software-update-available',
            f'有 {len(available)} 个应用可更新', text,
            ['default','查看更新','open','查看更新','later','稍后'],
            {'urgency':GLib.Variant('y',1),'desktop-entry':GLib.Variant('s','software-update-center')},0)),
        GLib.VariantType.new('(u)'),Gio.DBusCallFlags.NONE,10000,None)
    notification_id[0] = result.unpack()[0]
    print('Notification displayed: '+str(notification_id[0]),flush=True)
    def expire():
        try:
            bus.call_sync('org.freedesktop.Notifications','/org/freedesktop/Notifications',
                'org.freedesktop.Notifications','CloseNotification',
                GLib.Variant('(u)',(notification_id[0],)),None,Gio.DBusCallFlags.NONE,5000,None)
        finally:
            loop.quit()
        return False
    GLib.timeout_add_seconds(23*3600,expire)
    loop.run()
    bus.signal_unsubscribe(subscription)


def read_check(entry_point, *args, timeout):
    try:
        return json.loads(backend.run(['/usr/bin/python3', str(ROOT / entry_point), *args],
                                      timeout=timeout))
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(error, file=sys.stderr)
        return {}


def vendor_update(app_id):
    checked = read_check('official_apt.py', 'check', app_id, timeout=190)
    if (checked.get('installed') and not checked['held'] and
            backend.apt_pkg.version_compare(checked['version'], checked['installed']) > 0):
        return dict(name=catalog.APPS[app_id].name, available=True)
    return None


def collect_updates():
    # System APT and vendor probes run in separate subprocesses with separate indexes.
    with ThreadPoolExecutor(max_workers=3) as pool:
        system = pool.submit(read_check, 'backend.py', '--check', timeout=330)
        state = read_check('official_apt.py', 'status', timeout=30)
        eligible = [key for key, row in state.get('apps', {}).items()
                    if row['channel'] == 'apt' and row.get('installed') and row.get('uri')
                    and not row.get('error')]
        vendors = [pool.submit(vendor_update, key) for key in eligible]
        available = [row for row in system.result().get('apps', ()) if row['available']]
        available.extend(row for row in (future.result() for future in vendors) if row)
        return available


def main():
    notify({'apps': collect_updates()})

if __name__=='__main__':
    try:
        main()
    except Exception as error:
        print(str(error),file=sys.stderr)
        sys.exit(1)
