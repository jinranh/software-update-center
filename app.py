#!/usr/bin/python3
import json
from pathlib import Path
import subprocess
import sys
import threading
import uuid
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gio, GLib, Gdk
import backend
import catalog
from catalog_view import CatalogView, CSS, action, label, styled

ROOT = Path(__file__).resolve().parent


class Window(Gtk.ApplicationWindow):
    def __init__(self, application=None):
        title = '软件更新 · MVP 预览' if backend.PREVIEW else '软件更新'
        super().__init__(application=application, title=title)
        self.set_default_size(1060, 700)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.rows = []
        self.busy = False
        self.preparing = False
        self.batch_mode = False
        self.installing = False
        self.valid = False
        self.check_error = ''
        self.install_callback = None
        self.last_install = {}
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, margin=20)
        shell = styled(Gtk.Box(), 'catalog-shell')
        styled(outer, 'catalog-updates')
        self.add(shell)
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.catalog = CatalogView(self)
        self.stack.add_titled(self.catalog, 'catalog', '应用')
        self.stack.add_titled(outer, 'updates', '更新')
        sidebar = styled(Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=13),
                         'catalog-sidebar')
        brand = Gtk.Label(label='软件更新', xalign=0)
        styled(brand, 'catalog-product')
        sidebar.pack_start(brand, False, False, 0)
        switch = Gtk.StackSwitcher(orientation=Gtk.Orientation.VERTICAL, stack=self.stack)
        sidebar.pack_start(switch, False, False, 0)
        if backend.PREVIEW:
            sidebar.pack_end(label('只读预览', 'catalog-meta'), False, False, 0)
        shell.pack_start(sidebar, False, False, 0)
        shell.pack_start(self.stack, True, True, 0)
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider,
                                                 Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        header = Gtk.Box(spacing=12)
        header.pack_start(label('更新', 'catalog-heading'), True, True, 0)
        self.check_button = action('检查更新', self.check)
        self.check_button.set_valign(Gtk.Align.CENTER)
        header.pack_end(self.check_button, False, False, 0)
        self.batch_button = action('批量选择', self.toggle_batch)
        self.batch_button.set_valign(Gtk.Align.CENTER)
        self.batch_button.set_no_show_all(True)
        header.pack_end(self.batch_button, False, False, 0)
        outer.pack_start(header, False, False, 0)

        info = Gtk.Box(spacing=8)
        self.spinner = Gtk.Spinner()
        self.spinner.set_no_show_all(True)
        info.pack_start(self.spinner, False, False, 0)
        self.update_summary = label('正在检查更新…', 'catalog-meta')
        info.pack_start(self.update_summary, True, True, 0)
        self.check_details = action('检查详情', lambda *_: self.show_check_details())
        self.check_details.set_no_show_all(True)
        info.pack_end(self.check_details, False, False, 0)
        outer.pack_start(info, False, False, 0)

        self.status = label('', 'catalog-meta')
        self.status.set_no_show_all(True)
        self.status.connect('notify::label', lambda widget, _: widget.set_visible(bool(widget.get_text())))
        outer.pack_start(self.status, False, False, 0)
        self.official_status = label('', 'catalog-meta')
        self.official_status.set_no_show_all(True)
        outer.pack_start(self.official_status, False, False, 0)

        self.model = Gtk.ListStore(bool, str, str, str, str, str, bool)
        self.batch_checks = {}
        self.row_actions = []
        self.update_content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_end=12)
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroll.add(self.update_content)
        outer.pack_start(scroll, True, True, 0)

        self.footer = Gtk.Box(spacing=10)
        self.footer.set_no_show_all(True)
        self.select_button = action('全选', self.select_all)
        self.select_button.set_tooltip_text('选中所有可勾选的更新')
        self.footer.pack_start(self.select_button, False, False, 0)
        self.selected_label = label()
        self.footer.pack_start(self.selected_label, True, True, 0)
        self.install_button = action('更新所选', self.prepare, True)
        self.footer.pack_end(self.install_button, False, False, 0)
        for widget in self.footer.get_children():
            widget.show_all()
        outer.pack_start(self.footer, False, False, 0)
        self.refresh_updates()

    def update_row(self, name, installed, target, on_update, checkbox=None, failure=''):
        row = styled(Gtk.Box(spacing=10), 'catalog-card')
        if checkbox:
            checkbox.set_no_show_all(True)
            row.pack_start(checkbox, False, False, 0)
        details = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        details.pack_start(label(name, 'catalog-product'), False, False, 0)
        details.pack_start(label(f'{installed} → {target}', 'catalog-meta'), False, False, 0)
        if failure:
            details.pack_start(label(failure, 'catalog-error'), False, False, 0)
        row.pack_start(details, True, True, 0)
        button = action('重试' if failure else '更新', on_update, True)
        button.set_valign(Gtk.Align.CENTER)
        row.pack_end(button, False, False, 0)
        self.row_actions.append((button, checkbox is None))
        return row

    def checking_issues(self):
        issues = []
        if self.check_error:
            issues.append(('系统软件源', self.check_error))
        for app_id, row in self.catalog.state.get('apps', {}).items():
            if not row.get('installed'):
                continue
            app = catalog.APPS[app_id]
            if row['channel'] != 'apt':
                continue
            if row.get('error') or row.get('check_error'):
                issues.append((app.name, row.get('error') or row['check_error']))
            elif row.get('held'):
                issues.append((app.name, '版本已锁定。'))
            elif not row.get('uri'):
                issues.append((app.name, '在「应用」页设置厂商更新来源。'))
            elif row.get('checked') and not self.catalog.update_available(row):
                status, _ = self.catalog.version_status(app, row)
                if status != '已是最新':
                    issues.append((app.name, status))
        return issues

    def show_check_details(self):
        dialog = Gtk.Dialog(title='检查详情', transient_for=self, modal=True)
        dialog.set_default_size(640, 420)
        dialog.add_button('关闭', Gtk.ResponseType.CLOSE)
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=9, margin=16)
        for name, detail in self.checking_issues():
            content.pack_start(label(name, 'catalog-product'), False, False, 0)
            item = label(detail)
            item.set_selectable(True)
            content.pack_start(item, False, False, 0)
        scroll.add(content)
        dialog.get_content_area().pack_start(scroll, True, True, 0)
        dialog.show_all()
        dialog.run()
        dialog.destroy()

    def refresh_updates(self):
        if not hasattr(self, 'update_content'):
            return
        for child in self.update_content.get_children():
            child.destroy()
        self.batch_checks.clear()
        self.row_actions.clear()
        entries = self.catalog.state.get('apps', {})
        official = [(catalog.APPS[key], row) for key, row in entries.items()
                    if self.catalog.update_available(row) and not row.get('error')]
        native = [(row.path.to_string(), row) for row in self.model if self.valid and row[6]]
        for product, row in official:
            widget = self.update_row(product.name, row['installed'], row['version'],
                                     lambda _, app=product: self.catalog.preview(app))
            self.update_content.pack_start(widget, False, False, 0)
        for path, row in native:
            checkbox = Gtk.CheckButton()
            checkbox.get_accessible().set_name(f'选择 {row[1]}')
            checkbox.set_valign(Gtk.Align.CENTER)
            checkbox.set_active(row[0])
            checkbox.connect('toggled', self.update_choice, path)
            self.batch_checks[row[5]] = checkbox
            failure = row[4] if row[4] != '可更新' else ''
            widget = self.update_row(row[1], row[2], row[3],
                                     lambda _, name=row[5]: self.prepare(names=[name]), checkbox, failure)
            self.update_content.pack_start(widget, False, False, 0)

        issues = self.checking_issues()
        checking = self.busy or self.catalog.loading or bool(self.catalog.checking)
        count = len(official) + len(native)
        if count:
            summary = f'{count} 项可更新'
            if checking:
                summary += ' · 正在继续检查…'
        elif checking:
            summary = '正在检查更新…'
        elif issues:
            summary = '暂无可用更新'
        elif self.valid:
            summary = '已是最新'
        else:
            summary = '等待检查更新'
        if not count:
            empty = label(summary, 'update-empty')
            empty.set_xalign(0.5)
            empty.set_yalign(0.4)
            self.update_content.pack_start(empty, True, True, 0)
            summary = ''
        if issues:
            summary = f'{summary} · {len(issues)} 项需关注' if summary else f'{len(issues)} 项需关注'
        self.update_content.show_all()
        self.update_summary.set_text(summary)
        self.check_details.set_visible(bool(issues))
        self.official_status.set_text(self.catalog.message)
        self.official_status.set_visible(bool(self.catalog.message))
        self.spinner.set_visible(checking and bool(summary))
        if checking:
            self.spinner.start()
        else:
            self.spinner.stop()
        self.update_controls()

    def update_choice(self, checkbox, path):
        if self.model[path][0] != checkbox.get_active():
            self.toggle(None, path)

    def selection(self):
        return [row[5] for row in self.model if row[0] and row[6]]

    def toggle(self, renderer, path):
        row = self.model[path]
        if row[6] and not self.busy and not self.installing and self.valid:
            row[0] = not row[0]
            self.update_controls()

    def toggle_batch(self, button):
        self.batch_mode = not self.batch_mode
        if not self.batch_mode:
            for row in self.model:
                row[0] = False
        self.update_controls()

    def select_all(self, button):
        select = not all(row[0] for row in self.model if row[6])
        for row in self.model:
            row[0] = select and row[6]
        self.update_controls()

    def update_controls(self):
        count = len(self.selection())
        self.selected_label.set_text(f'已选择 {count} 项' if count else '')
        idle = not (self.busy or self.installing or self.catalog.active
                    or self.catalog.checking or self.catalog.loading)
        self.check_button.set_sensitive(idle)
        self.batch_button.set_visible(self.valid and (len(self.batch_checks) > 1 or bool(count)))
        self.batch_button.set_label('取消选择' if self.batch_mode else '批量选择')
        self.batch_button.set_sensitive(idle)
        self.select_button.set_sensitive(idle and self.valid and bool(self.batch_checks))
        self.select_button.set_label('取消全选' if count and count == len(self.batch_checks) else '全选')
        self.install_button.set_sensitive(idle and self.valid and count > 0)
        selecting = self.batch_mode or count > 0
        self.footer.set_visible(bool(self.batch_checks) and selecting)
        official_idle = not (self.preparing or self.installing or self.catalog.active
                             or self.catalog.checking or self.catalog.loading)
        for button, is_official in self.row_actions:
            button.set_sensitive(official_idle if is_official else idle)
        for row in self.model:
            checkbox = self.batch_checks.get(row[5])
            if checkbox:
                checkbox.set_visible(selecting)
                checkbox.set_sensitive(idle and self.valid)
                checkbox.set_active(row[0])

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
        if self.last_install:
            self.batch_mode = bool(self.selection())
        status = ''
        if self.last_install:
            updated = len(self.last_install.get('updated', []))
            status = f'{updated} 项已完成'
            if failures or pending:
                status += f' · {len(failures) + len(pending)} 项可重试'
            if self.last_install.get('message'):
                status += f' · {self.last_install["message"].splitlines()[-1][:250]}'
        self.status.set_text(status)
        self.busy = False
        self.check_error = ''
        self.catalog.render()
        return False

    def check_failed(self, message):
        self.valid = False
        self.busy = False
        self.check_error = str(message)[-3500:]
        self.status.set_text('系统软件源检查失败。')
        self.catalog.render()
        return False

    def update_failed(self, message):
        self.busy = self.preparing = False
        self.status.set_text(str(message)[-1000:])
        self.catalog.render()
        return False

    def check(self, button=None):
        self.catalog.refresh()
        return self.load_updates('--check')

    def load_updates(self, mode):
        if self.busy or self.installing:
            return False
        self.busy = True
        self.valid = False
        if mode == '--check':
            self.check_error = ''
        self.status.set_text('' if mode == '--check' else '正在核对安装结果…')
        self.catalog.render()
        def work():
            try:
                output = backend.run(['/usr/bin/python3', str(ROOT/'backend.py'), mode], timeout=330)
                GLib.idle_add(self.render, json.loads(output))
            except Exception as error:
                GLib.idle_add(self.check_failed, str(error))
        threading.Thread(target=work, daemon=True).start()
        return False

    def prepare(self, button=None, names=None):
        if self.busy or self.installing or self.catalog.active or not self.valid:
            return
        try:
            specs = backend.selected_specs(self.rows, names if names is not None else self.selection())
        except ValueError as error:
            self.update_failed(str(error))
            return
        self.busy = self.preparing = True
        self.status.set_text('正在准备更新计划…')
        self.catalog.render()
        def work():
            try:
                output = backend.preview(specs)
                GLib.idle_add(self.confirm, specs, output)
            except Exception as error:
                GLib.idle_add(self.update_failed, str(error))
        threading.Thread(target=work, daemon=True).start()

    def confirm(self, specs, output):
        self.busy = self.preparing = False
        self.status.set_text('')
        self.catalog.render()
        dialog = Gtk.Dialog(title='确认更新', transient_for=self, modal=True)
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
                self.update_failed(str(error))
            return
        self.install_callback = callback
        self.installing = True
        self.catalog.render()
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
                self.update_failed(str(error))
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
        super().__init__(application_id=app_id, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.window = None

    def do_command_line(self, command_line):
        args = command_line.get_arguments()[1:]
        if args not in ([], ['--updates']):
            print('Usage: app.py [--updates]', file=sys.stderr)
            return 2
        self.activate()
        if args == ['--updates']:
            self.window.stack.set_visible_child_name('updates')
        return 0

    def do_activate(self):
        if self.window is None:
            self.window = Window(self)
            self.window.connect('destroy', lambda *_: setattr(self, 'window', None))
            self.window.show_all()
            self.window.stack.set_visible_child_name('catalog')
            GLib.idle_add(self.window.check)
        self.window.present()

if __name__ == '__main__':
    sys.exit(Application().run(sys.argv))
