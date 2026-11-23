#!/usr/bin/python3
import datetime
import json
from pathlib import Path
import subprocess
import threading
import uuid
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gio, GLib, Gdk
import backend
from catalog_view import CatalogView, CSS, styled

ROOT = Path(__file__).resolve().parent


class Window(Gtk.ApplicationWindow):
    def __init__(self, application=None):
        title = '软件更新 · MVP 预览' if backend.PREVIEW else '软件更新'
        super().__init__(application=application, title=title)
        self.set_default_size(1060, 700)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.rows = []
        self.busy = False
        self.installing = False
        self.valid = False
        self.install_callback = None
        self.last_install = {}
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, margin=20)
        shell = styled(Gtk.Box(), 'catalog-shell')
        styled(outer, 'catalog-updates')
        self.add(shell)
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.catalog = CatalogView(self)
        self.stack.add_titled(self.catalog, 'catalog', '官方应用')
        self.stack.add_titled(outer, 'updates', '其他更新')
        sidebar = styled(Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=13),
                         'catalog-sidebar')
        brand = Gtk.Label(label='软件更新', xalign=0)
        styled(brand, 'catalog-product')
        sidebar.pack_start(brand, False, False, 0)
        switch = Gtk.StackSwitcher(orientation=Gtk.Orientation.VERTICAL, stack=self.stack)
        sidebar.pack_start(switch, False, False, 0)
        shell.pack_start(sidebar, False, False, 0)
        shell.pack_start(self.stack, True, True, 0)
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider,
                                                 Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        title = Gtk.Label(xalign=0)
        title.set_markup('<span size="x-large" weight="bold">其他更新</span>')
        outer.pack_start(title, False, False, 0)
        subtitle = Gtk.Label(label='勾选桌面应用以预览并安装 APT 更新。', xalign=0)
        outer.pack_start(subtitle, False, False, 0)
        bar = Gtk.Box(spacing=10)
        self.check_button = Gtk.Button(label='检查更新')
        self.check_button.connect('clicked', self.check)
        bar.pack_start(self.check_button, False, False, 0)
        self.select_button = Gtk.Button(label='选择全部可用更新')
        self.select_button.connect('clicked', self.select_all)
        bar.pack_start(self.select_button, False, False, 0)
        self.spinner = Gtk.Spinner()
        bar.pack_start(self.spinner, False, False, 0)
        outer.pack_start(bar, False, False, 0)
        self.status = Gtk.Label(label='正在准备检查……', xalign=0, selectable=True)
        self.status.set_line_wrap(True)
        outer.pack_start(self.status, False, False, 0)
        self.model = Gtk.ListStore(bool, str, str, str, str, str, bool)
        tree = Gtk.TreeView(model=self.model)
        toggle = Gtk.CellRendererToggle()
        toggle.connect('toggled', self.toggle)
        tree.append_column(Gtk.TreeViewColumn('选择', toggle, active=0, activatable=6, sensitive=6))
        for label, index in [('软件',1), ('当前版本',2), ('可用版本',3), ('状态',4)]:
            renderer = Gtk.CellRendererText()
            column = Gtk.TreeViewColumn(label, renderer, text=index)
            column.set_resizable(True)
            column.set_expand(index == 1)
            tree.append_column(column)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll.add(tree)
        outer.pack_start(scroll, True, True, 0)
        note = Gtk.Label(label='目录内的应用在「官方应用」页核对和更新。Firefox 等 Snap 应用由 Snap 管理。', xalign=0)
        note.set_line_wrap(True)
        outer.pack_start(note, False, False, 0)
        footer = Gtk.Box(spacing=10)
        self.selected_label = Gtk.Label(label='已选择 0 项', xalign=0)
        footer.pack_start(self.selected_label, True, True, 0)
        self.install_button = Gtk.Button(label='更新所选')
        self.install_button.get_style_context().add_class('suggested-action')
        self.install_button.connect('clicked', self.prepare)
        footer.pack_end(self.install_button, False, False, 0)
        outer.pack_start(footer, False, False, 0)
        self.update_controls()

    def selection(self):
        return [row[5] for row in self.model if row[0] and row[6]]

    def toggle(self, renderer, path):
        row = self.model[path]
        if row[6] and not self.busy and not self.installing and self.valid:
            row[0] = not row[0]
            self.update_controls()

    def select_all(self, button):
        select = not all(row[0] for row in self.model if row[6])
        for row in self.model:
            row[0] = select and row[6]
        self.update_controls()

    def update_controls(self):
        count = len(self.selection())
        self.selected_label.set_text(f'已选择 {count} 项')
        idle = not self.busy and not self.installing
        self.check_button.set_sensitive(idle)
        self.select_button.set_sensitive(idle and self.valid and any(row[6] for row in self.model))
        self.install_button.set_sensitive(idle and self.valid and count > 0)
        if self.busy:
            self.spinner.start()
        else:
            self.spinner.stop()
        self.catalog.render()

    def render(self, state):
        self.rows = state['apps']
        self.model.clear()
        self.valid = True
        failures = self.last_install.get('failures', {})
        pending = self.last_install.get('pending', [])
        for row in self.rows:
            status = '版本已锁定' if row['held'] else ('可更新' if row['available'] else '无需更新')
            package = row['package']
            retry = row['available'] and (package in failures or package in pending)
            if retry:
                status = failures.get(package, '上次未处理')
            self.model.append([retry, row['name'], row['installed'], row['candidate'],
                               status, package, row['available']])
        count = sum(row['available'] for row in self.rows)
        checked = datetime.datetime.fromtimestamp(state['checked_at']).strftime('%m-%d %H:%M')
        status = f'检查于 {checked} · {len(self.rows)} 个应用 · {count} 个可用更新'
        if self.last_install:
            updated = len(self.last_install.get('updated', []))
            status += f'\n上次更新：{updated} 项已完成，{len(failures)} 项未完成，{len(pending)} 项待处理。'
            message = self.last_install.get('message', '')
            if message:
                status += '\n' + message.splitlines()[-1][:250]
        self.status.set_text(status)
        self.busy = False
        self.update_controls()
        return False

    def check_failed(self, message):
        self.valid = False
        self.busy = False
        self.status.set_text('系统软件源检查失败，未确认最新版本。\n'
                             '「官方应用」仍可单独检查厂商源。\n' + str(message)[-1600:])
        self.update_controls()
        return False

    def check(self, button=None):
        return self.load_updates('--check')

    def load_updates(self, mode):
        if self.busy or self.installing:
            return False
        self.busy = True
        self.valid = False
        self.status.set_text('正在检查软件源，请稍候……' if mode == '--check' else '正在核对安装结果……')
        self.update_controls()
        def work():
            try:
                output = backend.run(['/usr/bin/python3', str(ROOT/'backend.py'), mode], timeout=330)
                GLib.idle_add(self.render, json.loads(output))
            except Exception as error:
                GLib.idle_add(self.check_failed, str(error))
        threading.Thread(target=work, daemon=True).start()
        return False

    def prepare(self, button=None):
        try:
            specs = backend.selected_specs(self.rows, self.selection())
        except ValueError as error:
            self.check_failed(str(error))
            return
        self.busy = True
        self.update_controls()
        self.status.set_text('正在计算软件及依赖变更……')
        def work():
            try:
                output = backend.preview(specs)
                GLib.idle_add(self.confirm, specs, output)
            except Exception as error:
                GLib.idle_add(self.check_failed, str(error))
        threading.Thread(target=work, daemon=True).start()

    def confirm(self, specs, output):
        self.busy = False
        self.update_controls()
        dialog = Gtk.Dialog(title='确认更新内容', transient_for=self, modal=True)
        dialog.set_default_size(780, 500)
        dialog.add_button('取消', Gtk.ResponseType.CANCEL)
        if not backend.PREVIEW:
            dialog.add_button('继续安装', Gtk.ResponseType.OK)
        box = dialog.get_content_area()
        box.set_spacing(12)
        box.set_border_width(16)
        label = Gtk.Label(label='以下是所选软件及依赖变更（Inst 为安装/升级）。\n输入本机密码后，系统会先下载安装包，再请你确认安装。\n若有下载失败，可安装其余可用项，失败项可回到窗口重试。', xalign=0)
        label.set_line_wrap(True)
        box.pack_start(label, False, False, 0)
        text = Gtk.TextView(editable=False, monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR)
        text.get_buffer().set_text(output)
        scroll = Gtk.ScrolledWindow()
        scroll.add(text)
        box.pack_start(scroll, True, True, 0)
        dialog.show_all()
        answer = dialog.run()
        dialog.destroy()
        if answer == Gtk.ResponseType.OK:
            self.launch_install(specs)
        return False

    def launch_install(self, specs, callback=None):
        if backend.PREVIEW or self.installing:
            if callback:
                callback({'code': 1, 'message': '当前为只读预览或已有安装任务。'})
            return
        token = uuid.uuid4().hex
        try:
            terminal = subprocess.Popen(['/usr/bin/gnome-terminal', '--wait', '--title=软件更新 — 安装', '--',
                            '/usr/bin/python3', str(ROOT/'install.py'), token, *specs],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as error:
            if callback:
                callback({'code': 1, 'message': str(error)})
            else:
                self.check_failed(str(error))
            return
        self.install_callback = callback
        self.installing = True
        self.update_controls()
        if callback is None:
            self.status.set_text('请在安装终端完成授权与确认。关闭本窗口不会中断安装。')
        GLib.timeout_add_seconds(2, self.poll_install, token, terminal)

    def poll_install(self, token, terminal):
        path = backend.CACHE / ('result-' + token + '.json')
        if not path.exists() and terminal.poll() is None:
            return True
        self.installing = False
        callback, self.install_callback = self.install_callback, None
        try:
            if not path.exists():
                raise RuntimeError('安装终端已关闭，未确认安装结果。请重新检查。')
            result = json.loads(path.read_text())
            path.unlink(missing_ok=True)
        except (OSError, ValueError, RuntimeError) as error:
            if callback:
                self.update_controls()
                callback({'code': 1, 'message': str(error)})
            else:
                self.check_failed(str(error))
            return False
        if callback:
            self.update_controls()
            callback(result)
            return False
        self.last_install = result
        if result.get('code') and not any(result.get(key) for key in ('failures', 'pending', 'message')):
            result['message'] = '安装未完成或已取消，详情见安装终端。'
        self.load_updates('--cached')
        return False


class Application(Gtk.Application):
    def __init__(self):
        app_id = 'local.software.UpdateCenter.Preview' if backend.PREVIEW else 'local.software.UpdateCenter'
        super().__init__(application_id=app_id, flags=Gio.ApplicationFlags.FLAGS_NONE)
        self.window = None
    def do_activate(self):
        if self.window is None:
            self.window = Window(self)
            self.window.connect('destroy', lambda *_: setattr(self, 'window', None))
            self.window.show_all()
            self.window.stack.set_visible_child_name('catalog')
            self.window.catalog.render()
            self.window.catalog.refresh()
            if not backend.PREVIEW:
                GLib.idle_add(self.window.check)
            else:
                self.window.status.set_text('只读预览。点击「检查更新」可在独立缓存中检查。')
        self.window.present()

if __name__ == '__main__':
    Application().run(None)
