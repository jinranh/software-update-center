"""GTK browser for catalog products and their independently checked APT channels."""
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
.catalog-sidebar { background: #172e34; color: #fff; padding: 22px 12px; }
.catalog-sidebar label { color: #fff; }
.catalog-sidebar button { background: transparent; border: 0; box-shadow: none; padding: 9px; }
.catalog-sidebar button:checked { background: #2e5358; }
.catalog-heading { font-size: 25px; font-weight: bold; }
.catalog-card { padding: 13px 15px; border: 1px solid alpha(currentColor, .22);
                border-radius: 10px; }
.catalog-icon { padding: 10px; border-radius: 8px; color: #196858; background: #d5eee6; }
.catalog-product { font-size: 16px; font-weight: bold; }
.catalog-meta { opacity: .8; }
.catalog-action { background: #176b5a; color: white; text-shadow: none; }
.catalog-action:disabled { background: alpha(#176b5a, .45); }
.catalog-error { color: shade(@error_color, .9); }
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
        self.checked = {}
        self.active = None
        self.loading = False
        self.message = ''
        self.failed = None
        self.pack_start(label('官方应用', 'catalog-heading'), False, False, 0)
        self.search = Gtk.SearchEntry(placeholder_text='搜索应用名称、别名或软件包')
        self.search.connect('search-changed', self.render)
        self.pack_start(self.search, False, False, 0)
        self.note = label()
        toolbar = Gtk.Box(spacing=8)
        toolbar.pack_start(self.note, True, True, 0)
        self.reload_button = action('重读本机状态', lambda *_: self.refresh())
        toolbar.pack_end(self.reload_button, False, False, 0)
        self.pack_start(toolbar, False, False, 0)
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
        if self.loading or self.active:
            return
        self.loading = True
        self.render()

        def work():
            try:
                state = json.loads(backend.run(['/usr/bin/python3', str(backend.ROOT / 'official_apt.py'),
                                            'status'], timeout=30))
                summaries = {app.id: appstream_summary(app) for app in catalog.APPS.values()}
                GLib.idle_add(self.loaded, state, summaries)
            except Exception as error:
                GLib.idle_add(self.load_failed, str(error))
        threading.Thread(target=work, daemon=True).start()

    def loaded(self, state, summaries):
        self.loading = False
        self.state, self.summaries = state, summaries
        self.render()
        return False

    def render(self, *_):
        for child in self.cards.get_children():
            child.destroy()
        if self.state:
            distro, version, arch = self.state['system']
            system = f'{distro.capitalize()} {version} · {arch}'
            self.note.set_text(system + (' · 只读预览' if backend.PREVIEW else ''))
        else:
            self.note.set_text('正在读取本机软件包和来源……' if self.loading or not self.message else
                               '无法读取本机状态。')
        self.reload_button.set_sensitive(not (self.loading or self.active))
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
            self.cards.pack_start(label('目录里没有匹配的官方应用。'), False, False, 10)
        for app in apps:
            self.cards.pack_start(self.card(app), False, False, 0)
        self.cards.show_all()

    def card(self, app):
        row = self.state.get('apps', {}).get(app.id, {})
        card = styled(Gtk.Box(spacing=13), 'catalog-card')
        icon = Gtk.Image.new_from_icon_name(app.icon, Gtk.IconSize.DIALOG)
        icon.set_valign(Gtk.Align.START)
        card.pack_start(styled(icon, 'catalog-icon'), False, False, 0)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        card.pack_start(body, True, True, 0)
        for text, style in (
                (app.name, 'catalog-product'),
                (f'{app.publisher}  ·  {app.package}', 'catalog-meta'),
                (self.summaries.get(app.id) or app.summary, None)):
            body.pack_start(label(text, style), False, False, 0)
        channel = row.get('channel')
        installed = row.get('installed', '')
        if installed:
            suffix = '' if row.get('official') else '（本机同包名，发布者未核验）'
            body.pack_start(label(f'已安装 {installed}{suffix}', 'catalog-meta'), False, False, 0)
        elif row:
            body.pack_start(label('未安装', 'catalog-meta'), False, False, 0)
        if channel == 'unavailable':
            distro, version, arch = self.state['system']
            details = f'{distro.capitalize()} {version} / {arch} 不在目录核实的支持范围。\n厂商支持 {app.support}'
        elif channel == 'deb':
            details = '来源  官方下载 · DEB\n' + app.update
        elif channel == 'apt':
            uri = row.get('uri') or app.repo.uri
            verified = self.checked.get(app.id)
            last_version = verified[1] if verified and verified[0] == row.get('uri') and not row.get('error') else ''
            cached_version = row.get('version')
            latest = (f' · 上次核验 {last_version}' if last_version else
                      f' · 缓存候选 {cached_version}' if cached_version else '')
            origin = '已配置' if row.get('uri') else '待启用'
            details = f'来源  厂商 APT 软件源（{origin}）{latest}\n{uri}\n更新  由此厂商 APT 源提供'
        else:
            details = '来源  厂商 APT 软件源' if app.repo else '来源  厂商官方文档'
        body.pack_start(label(details, 'catalog-meta'), False, False, 0)
        if row.get('error'):
            body.pack_start(label(row['error'], 'catalog-error'), False, False, 0)
        bar = Gtk.Box(spacing=6, valign=Gtk.Align.START, orientation=Gtk.Orientation.VERTICAL)
        card.pack_end(bar, False, False, 0)
        details_button = action('来源与证据', lambda *_: self.details(app, row))
        bar.pack_end(details_button, False, False, 0)
        busy = bool(self.loading or self.active or self.window.installing)
        if channel == 'apt' and not row.get('error'):
            if row.get('uri'):
                title = ('安装' if not installed else '更新' if row.get('available') else
                         '检查更新' if row.get('official') else '核对官方版本')
                if self.failed == app.id:
                    title = '重试'
                callback = lambda *_: self.preview(app)
            else:
                title = '重试启用' if self.failed == app.id else '启用厂商源'
                callback = lambda *_: self.enable(app)
            button = action(title, callback, True)
            button.set_sensitive(not busy and (bool(row.get('uri')) or not backend.PREVIEW))
            bar.pack_start(button, False, False, 0)
            if row.get('official'):
                bar.pack_start(action('打开', lambda *_: self.open_app(app)), False, False, 0)
        elif channel in ('deb', 'unavailable'):
            button = Gtk.LinkButton.new_with_label(app.website, '官方下载' if channel == 'deb' else '查看官方入口')
            bar.pack_start(button, False, False, 0)
        return card

    def details(self, app, row):
        dialog = Gtk.Dialog(title=f'{app.name} · 来源与证据', transient_for=self.window, modal=True)
        dialog.set_default_size(630, -1)
        dialog.add_button('关闭', Gtk.ResponseType.CLOSE)
        area = dialog.get_content_area()
        area.set_spacing(9)
        area.set_border_width(16)
        description = [f'发布者  {app.publisher}', f'DEB 软件包  {app.package}',
                       f'系统要求  {app.support}', f'更新方式  {app.update}']
        if app.repo:
            description.extend((f'仓库  {row.get("uri") or app.repo.uri}',
                                f'套件 / 组件  {app.repo.suite} / {app.repo.component}',
                                f'密钥地址  {app.repo.key_url}', f'主密钥指纹  {app.repo.fingerprint}'))
            if row.get('signed_by'):
                description.append(f'本机 Signed-By  {row["signed_by"]}')
        summary = label('\n'.join(description))
        summary.set_selectable(True)
        area.pack_start(summary, False, False, 0)
        for text, uri in app.evidence:
            area.pack_start(Gtk.LinkButton.new_with_label(uri, text), False, False, 0)
        dialog.show_all()
        dialog.run()
        dialog.destroy()

    def enable(self, app):
        if backend.PREVIEW or self.loading or self.active or self.window.installing:
            return
        message = (f'{app.publisher} · {app.package}\n\n{app.repo.uri}\n'
                   f'{app.repo.suite} / {app.repo.component}\n\n'
                   f'公钥  {app.repo.key_url}\n主密钥指纹  {app.repo.fingerprint}\n'
                   f'Signed-By  /etc/apt/keyrings/software-update-center-{app.id}.asc\n\n'
                   '启用后可在窗口审查候选包和依赖。')
        if self.confirm('启用厂商软件源', message, '授权启用'):
            self.authorize(app, 'enable')

    def preview(self, app):
        if self.loading or self.active or self.window.installing:
            return
        self.active = app.id
        self.message = f'正在单独刷新 {app.publisher} 软件源并核对 APT 候选包……'
        self.render()

        def work():
            try:
                plan = json.loads(backend.run(['/usr/bin/python3', str(backend.ROOT / 'official_apt.py'),
                                               'plan', app.id], timeout=220))
                GLib.idle_add(self.confirm_plan, app, plan)
            except Exception as error:
                GLib.idle_add(self.failed_operation, app.id, str(error))
        threading.Thread(target=work, daemon=True).start()

    def confirm_plan(self, app, plan):
        self.active = self.failed = None
        self.checked[app.id] = (plan['source'], plan['version'])
        row = self.state['apps'][app.id]
        row['available'] = bool(plan['previous'] and plan['previous'] != plan['version'])
        previous = plan['previous']
        if previous and previous == plan['version'] and row.get('official'):
            self.message = f'{app.name} 已是当前厂商候选版本 {previous}。'
            self.render()
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

    def authorize(self, app, operation, *args):
        self.active = app.id
        self.message = f'{app.name} · 请在安装终端完成授权与确认。'
        self.render()
        self.window.launch_install(['--official', operation, app.id, *args], self.finished)

    def finished(self, result):
        self.failed = self.active if result['code'] else None
        self.active = None
        self.message = result.get('message') or '安装未完成或已取消，请重试。'
        self.render()
        self.refresh()

    def load_failed(self, message):
        self.loading = False
        self.message = message[-1900:]
        self.render()
        return False

    def failed_operation(self, app_id, message):
        self.active = None
        self.failed = app_id
        self.checked.pop(app_id, None)
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
            self.failed_operation(app.id, str(error))
