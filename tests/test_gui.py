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
                        'apps': {'chatgpt': dict(channel='unavailable', installed='', error='')}}, {})
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
        self.assertTrue(any('OpenAI' in text and 'chatgpt' in text for text in labels))
        self.assertTrue(any('Ubuntu 22.04' in text and '24.04' in text for text in labels))
        self.assertEqual(set(buttons), {'查看官方入口', '来源与证据'})
        window.destroy()


if __name__=='__main__': unittest.main()
