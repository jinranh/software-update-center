import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gi
gi.require_version('Gtk','3.0')
from gi.repository import Gtk
import app

class WindowTests(unittest.TestCase):
    def test_only_available_rows_can_be_selected(self):
        window=app.Window()
        window.render({'checked_at':0,'apps':[
            dict(package='code',name='VS Code',installed='1',candidate='2',held=False,available=True),
            dict(package='chatgpt',name='ChatGPT',installed='2',candidate='2',held=False,available=False)]})
        window.toggle(None, '1')
        self.assertEqual(window.selection(), [])
        window.toggle(None, '0')
        self.assertEqual(window.selection(), ['code'])
        window.destroy()
    def test_closed_terminal_unlocks_the_window(self):
        import subprocess
        window=app.Window()
        window.installing=True
        process=subprocess.Popen(['/bin/true']);process.wait()
        self.assertFalse(window.poll_install('0'*32,process))
        self.assertFalse(window.installing)
        self.assertTrue(window.check_button.get_sensitive())
        self.assertIn('未确认安装结果',window.status.get_text())
        window.destroy()

    def test_error_does_not_claim_up_to_date(self):
        window=app.Window()
        window.check_failed('network unavailable')
        self.assertIn('检查失败',window.status.get_text())
        self.assertFalse(window.install_button.get_sensitive())
        window.destroy()

    def test_partial_result_keeps_only_failed_update_selected(self):
        window = app.Window()
        window.last_install = dict(code=100, updated=['chatgpt'],
                                   failures={'claude-desktop': '下载超时'}, pending=[])
        window.render({'checked_at': 0, 'apps': [
            dict(package='chatgpt', name='ChatGPT', installed='2', candidate='2',
                 held=False, available=False),
            dict(package='claude-desktop', name='Claude', installed='1', candidate='2',
                 held=False, available=True)]})
        self.assertEqual(window.selection(), ['claude-desktop'])
        self.assertIn('下载超时', window.model[1][4])
        self.assertIn('1 项已完成', window.status.get_text())
        self.assertTrue(window.install_button.get_sensitive())
        window.destroy()

    def test_catalog_shows_single_official_chatgpt_and_jammy_notice(self):
        window = app.Window()
        catalog = window.catalog
        catalog.loaded({'system': ('ubuntu', '22.04', 'amd64'),
                        'apps': {'chatgpt': dict(channel='unavailable', installed='26.1117.41037', error='')}}, {})
        catalog.search.set_text('chatgpt')
        catalog.render()
        cards = catalog.cards.get_children()
        self.assertEqual(len(cards), 1)
        stack = [cards[0]]
        labels = []
        buttons = []
        while stack:
            widget = stack.pop()
            if isinstance(widget, Gtk.Label):
                labels.append(widget.get_text())
            if isinstance(widget, Gtk.Button):
                buttons.append(widget.get_label())
            if isinstance(widget, Gtk.Container):
                stack.extend(widget.get_children())
        self.assertIn('OpenAI', labels)
        self.assertIn('已安装 26.1117.41037 · Ubuntu 22.04 暂无官方更新', labels)
        self.assertEqual(set(buttons), {'详情'})
        window.destroy()

    def test_installed_catalog_card_has_clear_version_states_in_preview(self):
        from unittest.mock import patch
        window = app.Window()
        view = window.catalog
        view.loaded({'system': ('ubuntu', '22.04', 'amd64'), 'apps': {
            'vscode': dict(channel='apt', installed='1', uri='https://packages.microsoft.com/repos/code')
        }}, {})
        view.search.set_text('VS Code')
        view.render()

        def widgets(widget, kind):
            if isinstance(widget, kind):
                yield widget
            if isinstance(widget, Gtk.Container):
                for child in widget.get_children():
                    yield from widgets(child, kind)

        def visible_status():
            return ' '.join(item.get_text() for item in widgets(view.cards, Gtk.Label))

        self.assertIn('待检查', visible_status())
        with patch('backend.PREVIEW', True):
            view.check_result('vscode', dict(installed='1', version='2', newest='2',
                                            official=False, held=False))
            self.assertIn('有更新', visible_status())
            buttons = {item.get_label(): item for item in widgets(view.cards, Gtk.Button)}
            self.assertIn('更新', buttons)
            self.assertTrue(buttons['更新'].get_sensitive())
            view.check_result('vscode', dict(installed='2', version='2', newest='2',
                                            official=False, held=False))
            self.assertIn('已是最新', visible_status())
            self.assertNotIn('已安装 2', visible_status())
            self.assertNotIn('安装来源待核验', visible_status())
            self.assertNotIn('更新', {item.get_label() for item in widgets(view.cards, Gtk.Button)})
            view.check_result('vscode', dict(installed='2', version='2', newest='3',
                                            official=False, held=False))
            self.assertIn('厂商有新版 3', visible_status())
            self.assertNotIn('已是最新', visible_status())
            view.check_result('vscode', error='网络无法连接')
            self.assertIn('检查失败', visible_status())
            self.assertNotIn('已是最新', visible_status())
        window.destroy()

    def test_updates_page_combines_channels_and_omits_up_to_date_apps(self):
        window = app.Window()
        window.catalog.loaded({'system': ('ubuntu', '22.04', 'amd64'), 'apps': {
            'vscode': dict(channel='apt', installed='1', uri='https://packages.microsoft.com/repos/code'),
            'claude': dict(channel='apt', installed='1', uri='https://downloads.claude.ai/claude-desktop/apt/stable')
        }}, {})
        window.catalog.check_result('vscode', dict(installed='1', version='2', newest='2',
                                                    official=False, held=False))
        window.catalog.check_result('claude', dict(installed='1', version='1', newest='1',
                                                    official=False, held=False))
        window.render({'checked_at': 0, 'apps': [
            dict(package='editor', name='Text Editor', installed='1', candidate='2',
                 held=False, available=True),
            dict(package='calculator', name='Calculator', installed='2', candidate='2',
                 held=False, available=False),
        ]})

        def labels(widget):
            if isinstance(widget, Gtk.Label):
                yield widget.get_text()
            if isinstance(widget, Gtk.Container):
                for child in widget.get_children():
                    yield from labels(child)

        visible = set(labels(window.stack.get_child_by_name('updates')))
        self.assertIn('Visual Studio Code', visible)
        self.assertIn('Text Editor', visible)
        self.assertNotIn('Claude Desktop', visible)
        self.assertNotIn('Calculator', visible)
        window.check_failed('测试源不可达')
        self.assertTrue(window.check_details.get_visible())
        self.assertIn('Visual Studio Code', set(labels(window.update_content)))
        self.assertTrue(any(button.get_sensitive() for button, official in window.row_actions if official))
        self.assertEqual(window.stack.child_get_property(window.catalog, 'title'), '应用')
        self.assertEqual(window.stack.child_get_property(window.stack.get_child_by_name('updates'), 'title'), '更新')
        window.destroy()

    def test_empty_search_can_return_to_the_curated_catalog(self):
        window = app.Window()
        view = window.catalog
        view.search.set_text('微信')
        view.render()
        message, clear = view.cards.get_children()
        self.assertEqual(message.get_text(), '暂未收录该应用。')
        clear.clicked()
        view.render()
        self.assertEqual(view.search.get_text(), '')
        self.assertEqual(len(view.cards.get_children()), 6)
        window.destroy()



if __name__=='__main__': unittest.main()
