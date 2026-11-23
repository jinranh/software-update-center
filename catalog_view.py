"""GTK browser for catalog products and their independently checked APT channels."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
import re
import threading
import subprocess

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gio, GLib, Gtk, Pango

import backend
import catalog
from official_apt import plan_digest


CSS = b"""
.catalog-shell { background: #f5f6f8; }
.catalog-sidebar { background: #e9edf2; padding: 24px 14px; min-width: 150px; }
.catalog-sidebar label { color: #223041; }
.catalog-sidebar button { background: transparent; border: 0; border-radius: 9px;
                          box-shadow: none; text-shadow: none; padding: 10px 13px; }
.catalog-sidebar button:hover { background: #e0e7ef; }
.catalog-sidebar button:checked { background: #d4e2ee; }
.catalog-view label, .catalog-updates label { color: #202d3b; }
.catalog-updates label.catalog-meta { color: #536477; }
.catalog-updates label.catalog-error { color: #9c3238; }
.catalog-updates label.update-empty { font-size: 19px; color: #536477; }
.catalog-updates check { background: #f7fafe; border: 1px solid #8195a7; border-radius: 4px; }
.catalog-updates check:checked { background: #16649b; border-color: #16649b; color: #fff; }
.catalog-view label.catalog-meta { color: #536477; }
.catalog-heading { font-size: 27px; font-weight: bold; }
.catalog-search { background: #e8edf3; color: #243346; border: 1px solid #e8edf3;
                  border-radius: 10px; box-shadow: none; padding: 8px 11px; }
.catalog-search:focus { border-color: #477aab; }
.catalog-search placeholder { color: #5e6d7c; }
.catalog-card { padding: 10px 14px; background: #fff; border: 1px solid #dde5ec;
                border-radius: 14px; }
.catalog-icon { padding: 8px; border-radius: 11px; color: #216670; background: #e1f0f1; }
.catalog-product { font-size: 17px; font-weight: bold; }
.catalog-view label.catalog-latest { color: #285f55; }
.catalog-view label.catalog-update { color: #0e5795; }
.catalog-view label.catalog-error { color: #9c3238; }
.catalog-view button,
.catalog-updates button { border-radius: 14px; box-shadow: none; text-shadow: none;
                       min-height: 25px; min-width: 64px; padding: 2px 10px; }
.catalog-view button:not(.catalog-action):not(.link),
.catalog-updates button:not(.catalog-action):not(.link) { background: #edf2f7; color: #344e66;
                                                     border: 1px solid #dfe7ef; }
.catalog-view button:not(.catalog-action):not(.link):hover,
.catalog-updates button:not(.catalog-action):not(.link):hover { background: #e2ebf3; }
.catalog-view button:not(.catalog-action):not(.link):disabled,
.catalog-updates button:not(.catalog-action):not(.link):disabled { color: #697d92; }
.catalog-view button.link,
.catalog-updates button.link { color: #215b89; }
.catalog-view button.catalog-action,
.catalog-updates button.catalog-action { background: #16649b; color: #fff; border: 1px solid #16649b; }
.catalog-view button.catalog-action:hover,
.catalog-updates button.catalog-action:hover { background: #105786; }
.catalog-view button.catalog-action label,
.catalog-updates button.catalog-action label { color: #fff; }
.catalog-view button.catalog-action:disabled label,
.catalog-updates button.catalog-action:disabled label { color: #697d92; }
.catalog-view button.catalog-action:disabled,
.catalog-updates button.catalog-action:disabled { background: #e7eef4; color: #697d92;
                                              border-color: #e7eef4; }
"""


def styled(widget, name):
    widget.get_style_context().add_class(name)
    return widget


def label(text='', style=None):
    item = Gtk.Label(label=text, xalign=0, selectable=False)
    item.set_line_wrap(True)
    item.set_max_width_chars(86)
    item.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
    return styled(item, style) if style else item


def action(text, callback, emphasized=False):
    button = Gtk.Button(label=text)
    if emphasized:
        styled(button, 'catalog-action')
    button.connect('clicked', callback)
    return button


def appstream_summary(app):
    component = app.appstream_id or app.desktop_id
    try:
        result = subprocess.run(['/usr/bin/appstreamcli', 'get', component], timeout=4,
                                capture_output=True, text=True, errors='replace',
                                env={**backend.ENV, 'LC_ALL': 'C.UTF-8', 'LANGUAGE': 'C'})
        output = result.stdout
    except FileNotFoundError:
        return ''
    except (RuntimeError, subprocess.TimeoutExpired):
        return ''
    ids = re.findall(r'^(?:Identifier|ID):\s*(\S+)', output, re.M)
    packages = re.findall(r'^Package:\s*(\S+)', output, re.M)
    summary = re.search(r'^Summary:\s*(.+)', output, re.M)
    # A local catalog with another package for the same desktop ID is ignored.
    if ids == [component] and packages == [app.package] and summary:
        return summary[1][:240]
    return ''


class CatalogView(Gtk.Box):
    def __init__(self, window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin=18)
        self.window = window
        self.state = {}
        self.summaries = {}
        self.checking = set()
        styled(self, 'catalog-view')
        self.active = None
        self.loading = False
        self.message = ''
        self.pack_start(label('应用', 'catalog-heading'), False, False, 0)
        self.search = styled(Gtk.SearchEntry(placeholder_text=f'搜索已收录的 {len(catalog.APPS)} 款应用'), 'catalog-search')
        self.search.connect('search-changed', self.render)
        self.pack_start(self.search, False, False, 0)
        self.notice = Gtk.Box(spacing=9)
        self.spinner = Gtk.Spinner()
        self.notice.pack_start(self.spinner, False, False, 0)
        self.progress = label('', 'catalog-error')
        self.progress.set_selectable(True)
        self.notice.pack_start(self.progress, True, True, 0)
        self.pack_start(self.notice, False, False, 0)
        self.cards = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=9, margin_end=12)
        self.scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.scroll.add(self.cards)
        self.pack_start(self.scroll, True, True, 0)
        self.render()

    def refresh(self):
        if self.loading or self.active or self.checking or self.window.installing or self.window.preparing:
            return
        self.loading = True
        self.render()

        def work():
            try:
                state = json.loads(backend.run(['/usr/bin/python3', str(backend.ROOT / 'official_apt.py'),
                                            'status'], timeout=30))
                summaries = {app.id: appstream_summary(app) for app in catalog.APPS.values()}
                GLib.idle_add(self.loaded, state, summaries, True)
            except Exception as error:
                GLib.idle_add(self.load_failed, str(error))
        threading.Thread(target=work, daemon=True).start()

    def loaded(self, state, summaries, check_installed=False):
        self.loading = False
        self.state, self.summaries = state, summaries
        if check_installed:
            self.check_versions([key for key, row in state['apps'].items() if row['installed']])
        self.render()
        return False

    def check_versions(self, app_ids):
        rows = self.state.get('apps', {})
        selected = {key for key in app_ids
                    if rows[key]['channel'] == 'apt' and rows[key].get('uri') and not rows[key].get('error')}
        if not selected or self.active or self.checking or self.window.installing:
            return
        self.checking = selected
        self.render()

        def check(app_id):
            return json.loads(backend.run(['/usr/bin/python3', str(backend.ROOT / 'official_apt.py'),
                                           'check', app_id], timeout=190))

        def work():
            with ThreadPoolExecutor(max_workers=3) as executor:
                pending = {executor.submit(check, key): key for key in selected}
                for job in as_completed(pending):
                    try:
                        result, error = job.result(), ''
                    except Exception as failure:
                        result, error = None, str(failure)
                    GLib.idle_add(self.check_result, pending[job], result, error)

        threading.Thread(target=work, daemon=True).start()

    def check_result(self, app_id, result=None, error=''):
        self.checking.discard(app_id)
        row = self.state['apps'][app_id]
        row['check_error'] = error[-1900:]
        row['checked'] = bool(result)
        if result:
            row.update(result)
        self.render()
        return False

    def render(self, *_):
        for child in self.cards.get_children():
            child.destroy()
        self.progress.set_text(self.message)
        if self.active:
            self.spinner.show()
            self.spinner.start()
        else:
            self.spinner.stop()
            self.spinner.hide()
        if not self.message and not self.active:
            self.notice.hide()
        else:
            self.notice.show()
        apps = catalog.search(self.search.get_text())
        if not apps:
            self.cards.pack_start(label('暂未收录该应用。', 'catalog-meta'), False, False, 10)
            clear = action('查看全部应用', lambda *_: self.search.set_text(''))
            clear.set_halign(Gtk.Align.START)
            self.cards.pack_start(clear, False, False, 0)
        for app in apps:
            self.cards.pack_start(self.card(app), False, False, 0)
        self.cards.show_all()
        self.window.refresh_updates()

    def card(self, app):
        row = self.state.get('apps', {}).get(app.id, {})
        card = styled(Gtk.Box(spacing=13), 'catalog-card')
        icon = Gtk.Image.new_from_icon_name(app.icon, Gtk.IconSize.DIALOG)
        icon.set_pixel_size(26)
        icon.set_valign(Gtk.Align.CENTER)
        card.pack_start(styled(icon, 'catalog-icon'), False, False, 0)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        card.pack_start(body, True, True, 0)
        body.pack_start(label(app.name, 'catalog-product'), False, False, 0)
        body.pack_start(label(app.publisher, 'catalog-meta'), False, False, 0)
        channel = row.get('channel')
        installed = row.get('installed', '')
        if channel == 'unavailable':
            distro, version, _ = self.state['system']
            system = f'{distro.capitalize()} {version}'
            status = (f'已安装 {installed} · {system} 暂无官方更新' if installed
                      else f'{system} 暂无官方版本')
            body.pack_start(label(status, 'catalog-meta'), False, False, 0)
        elif channel == 'deb':
            status = '已安装 · 更新由厂商提供' if installed else '通过官网安装'
            body.pack_start(label(status, 'catalog-meta'), False, False, 0)
        elif channel == 'apt':
            status, style = self.version_status(app, row)
            body.pack_start(label(status, style), False, False, 0)

        bar = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
        card.pack_end(bar, False, False, 0)
        bar.pack_end(action('详情', lambda *_: self.details(app, row)), False, False, 0)
        busy = bool(self.loading or self.active or self.checking or self.window.installing or self.window.preparing)
        if row.get('repair'):
            button = action('审查来源修复', lambda *_: self.preview_repair(app), True)
            button.set_sensitive(not busy)
            bar.pack_start(button, False, False, 0)
        elif channel == 'apt' and not row.get('error'):
            if row.get('check_error'):
                button = action('重试', lambda *_: self.check_versions([app.id]), True)
            elif not row.get('uri'):
                button = action('设置更新' if installed else '安装', lambda *_: self.enable(app), True)
            elif not installed:
                button = action('安装', lambda *_: self.preview(app), True)
            elif self.update_available(row):
                button = action('更新', lambda *_: self.preview(app), True)
            else:
                button = None
            if button is not None:
                if backend.PREVIEW and button.get_label() == '更新':
                    button.set_tooltip_text('只读预览可查看更新计划')
                button.set_sensitive(not busy)
                bar.pack_start(button, False, False, 0)
            if row.get('official') and button is None:
                bar.pack_start(action('打开', lambda *_: self.open_app(app)), False, False, 0)
        elif channel == 'deb' and not installed:
            bar.pack_start(Gtk.LinkButton.new_with_label(app.website, '官方下载'), False, False, 0)
        return card

    def version_status(self, app, row):
        installed = row.get('installed')
        error = row.get('error', '')
        if '重复' in error:
            return '有重复软件源，暂时无法更新', 'catalog-error'
        if row.get('held') or '已锁定' in error:
            return '版本已锁定', 'catalog-meta'
        if error:
            return '无法检查更新', 'catalog-error'
        if row.get('check_error'):
            return '检查失败，请重试', 'catalog-error'
        if not installed:
            return '未安装', 'catalog-meta'
        if app.id in self.checking:
            return '正在检查更新…', 'catalog-meta'
        if not row.get('uri'):
            return '更新来源待设置', 'catalog-meta'
        if not row.get('checked'):
            return '待检查', 'catalog-meta'
        target, newest = row['version'], row['newest']
        if self.update_available(row):
            return f'有更新 · {target}', 'catalog-update'
        if backend.apt_pkg.version_compare(newest, installed) > 0:
            return f'厂商有新版 {newest} · 当前不可更新', 'catalog-meta'
        return '已是最新', 'catalog-latest'

    def update_available(self, row):
        return (bool(row.get('checked') and row.get('installed')) and not row.get('held')
                and backend.apt_pkg.version_compare(row['version'], row['installed']) > 0)

    def details(self, app, row):
        dialog = Gtk.Dialog(title=app.name, transient_for=self.window, modal=True)
        dialog.set_default_size(630, -1)
        dialog.add_button('关闭', Gtk.ResponseType.CLOSE)
        if (row.get('checked') and not row.get('official') and row.get('installed') == row.get('version')
                and not row.get('error') and not row.get('held')):
            dialog.add_button('安装官方版', Gtk.ResponseType.APPLY)
        area = dialog.get_content_area()
        area.set_spacing(9)
        area.set_border_width(16)
        description = [self.summaries.get(app.id) or app.summary,
                       f'发布者  {app.publisher}', f'DEB 软件包  {app.package}',
                       f'系统要求  {app.support}', f'更新方式  {app.update}']
        if row.get('installed'):
            description.append(f'本机版本  {row["installed"]}')
            if not row.get('official'):
                description.append('本机安装来源未核验。')
        if row.get('checked'):
            description.append(f'厂商最新版本  {row["newest"]}')
        if app.repo:
            description.extend((f'仓库  {row.get("uri") or app.repo.uri}',
                                f'套件 / 组件  {app.repo.suite} / {app.repo.component}',
                                f'密钥地址  {app.repo.key_url}', f'主密钥指纹  {app.repo.fingerprint}'))
            if row.get('signed_by'):
                description.append(f'本机 Signed-By  {row["signed_by"]}')
        error = row.get('error') or row.get('check_error')
        if error:
            description.append(error)
        summary = label('\n'.join(description))
        summary.set_selectable(True)
        area.pack_start(summary, False, False, 0)
        if all(uri != app.website for _, uri in app.evidence):
            area.pack_start(Gtk.LinkButton.new_with_label(app.website, '厂商官网'), False, False, 0)
        for text, uri in app.evidence:
            area.pack_start(Gtk.LinkButton.new_with_label(uri, text), False, False, 0)
        dialog.show_all()
        response = dialog.run()
        dialog.destroy()
        if response == Gtk.ResponseType.APPLY:
            self.preview(app, reinstall=True)

    def enable(self, app):
        if self.loading or self.active or self.checking or self.window.installing or self.window.preparing:
            return
        message = (f'{app.publisher} · {app.package}\n\n{app.repo.uri}\n'
                   f'{app.repo.suite} / {app.repo.component}\n\n'
                   f'公钥  {app.repo.key_url}\n主密钥指纹  {app.repo.fingerprint}\n'
                   f'Signed-By  /etc/apt/keyrings/software-update-center-{app.id}.asc\n\n'
                   '启用后可在窗口审查候选包和依赖。')
        if backend.PREVIEW:
            message += '\n\n只读预览，启用和安装需通过正式窗口授权。'
        if self.confirm(f'安装 {app.name} · 确认来源', message,
                        None if backend.PREVIEW else '授权启用'):
            self.authorize(app, 'enable', on_success=lambda: self.source_enabled(app))

    def source_enabled(self, app):
        self.state['apps'][app.id]['uri'] = app.repo.uri
        self.preview(app)

    def preview_repair(self, app):
        if self.loading or self.active or self.checking or self.window.installing or self.window.preparing:
            return
        self.active = app.id
        self.message = '正在单独验证保留的 Google Chrome 软件源……'
        self.render()

        def work():
            try:
                command = ['/usr/bin/python3', str(backend.ROOT / 'official_apt.py'), 'repair-plan', app.id]
                plan = json.loads(backend.run(command, timeout=190))
                GLib.idle_add(self.confirm_repair, app, plan)
            except Exception as error:
                GLib.idle_add(self.failed_operation, str(error))
        threading.Thread(target=work, daemon=True).start()

    def confirm_repair(self, app, plan):
        self.active = None
        self.message = ''
        self.render()
        description = (f'保留 {plan["canonical"]}\n{plan["source"]}\n'
                       f'Signed-By  {plan["signed_by"]}\n签名指纹  {plan["fingerprint"]}\n'
                       f'已验证候选版本 {plan["version"]}\n\n停用 {plan["legacy"]}\n'
                       f'原文件备份至 {plan["backup"]}。')
        if self.confirm('审查 Chrome 软件源修复', description,
                        None if backend.PREVIEW else '授权停用旧源'):
            self.authorize(app, 'repair-apply', plan_digest(plan))
        return False

    def preview(self, app, reinstall=False):
        if self.loading or self.active or self.checking or self.window.installing or self.window.preparing:
            return
        self.active = app.id
        self.message = f'正在单独刷新 {app.publisher} 软件源并核对 APT 候选包……'
        self.render()

        def work():
            try:
                plan = json.loads(backend.run(['/usr/bin/python3', str(backend.ROOT / 'official_apt.py'),
                                               'plan', app.id], timeout=220))
                GLib.idle_add(self.confirm_plan, app, plan, reinstall)
            except Exception as error:
                GLib.idle_add(self.failed_operation, str(error))
        threading.Thread(target=work, daemon=True).start()

    def confirm_plan(self, app, plan, reinstall=False):
        self.active = None
        row = self.state['apps'][app.id]
        previous = plan['previous']
        if previous and previous == plan['version'] and not reinstall:
            self.message = f'{app.name} 当前没有可安装的新版。'
            self.refresh()
            return False
        verb = '安装' if not previous else '重新安装' if previous == plan['version'] else '更新'
        self.message = f'{app.name} · 已验证的 APT 候选 {plan["version"]}'
        self.render()
        header = f'{app.publisher} · {app.package}  {previous or "未安装"} → {plan["version"]}\n{plan["source"]}'
        if previous and not row.get('official'):
            header += '\n本机同名包的原发布者未核验。继续将使用已核验的官方包替换。'
        if backend.PREVIEW:
            header += '\n只读预览。'
        archives = '\n\n'.join(
            f'{item["package"]} ({item["arch"]})  {item["previous"] or "未安装"} → {item["version"]}\n'
            f'{item["uri"]}' for item in plan['archives']
        )
        if self.confirm(f'审查{verb}与依赖', header, f'授权{verb}' if not backend.PREVIEW else None,
                        archives):
            self.authorize(app, 'apply', plan_digest(plan))
        return False

    def confirm(self, title, header, proceed, archives=None):
        dialog = Gtk.Dialog(title=title, transient_for=self.window, modal=True)
        dialog.set_default_size(730, 420 if archives else -1)
        dialog.add_button('关闭' if not proceed else '取消', Gtk.ResponseType.CANCEL)
        if proceed:
            dialog.add_button(proceed, Gtk.ResponseType.OK)
        dialog.set_default_response(Gtk.ResponseType.CANCEL)
        area = dialog.get_content_area()
        area.set_spacing(12)
        area.set_border_width(16)
        summary = label(header)
        summary.set_selectable(True)
        area.pack_start(summary, False, False, 0)
        if archives:
            text = Gtk.TextView(editable=False, monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR)
            text.get_buffer().set_text(archives)
            scroller = Gtk.ScrolledWindow()
            scroller.add(text)
            area.pack_start(scroller, True, True, 0)
        dialog.show_all()
        response = dialog.run()
        dialog.destroy()
        return response == Gtk.ResponseType.OK

    def authorize(self, app, operation, *args, on_success=None):
        self.active = app.id
        self.message = f'{app.name} · 请在安装终端完成授权与确认。'
        self.render()
        self.window.launch_install(['--official', operation, app.id, *args],
                                   lambda result: self.finished(result, on_success))

    def finished(self, result, on_success):
        self.active = None
        self.message = result.get('message') or '安装未完成或已取消，请重试。'
        self.render()
        if result['code'] == 0 and on_success:
            on_success()
        else:
            self.refresh()

    def load_failed(self, message):
        self.loading = False
        self.message = message[-1900:]
        self.render()
        return False

    def failed_operation(self, message):
        self.active = None
        self.message = message[-1900:]
        self.render()
        return False

    def open_app(self, app):
        try:
            desktop = Path('/usr/share/applications') / app.desktop_id
            owner = backend.run(['/usr/bin/dpkg-query', '-S', str(desktop)])
            if owner.split(':', 1)[0] != app.package:
                raise RuntimeError(f'桌面启动器不属于 {app.package}。')
            from official_apt import local_version, official_installation
            if not official_installation(app, local_version(app.package)):
                raise RuntimeError('安装记录已变化，请重新核对厂商包。')
            launcher = Gio.DesktopAppInfo.new_from_filename(str(desktop))
            if launcher is None or not launcher.launch([], None):
                raise RuntimeError('无法启动系统桌面应用。')
        except Exception as error:
            self.failed_operation(str(error))
