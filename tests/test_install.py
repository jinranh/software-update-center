import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import install

class InstallTests(unittest.TestCase):
    def test_lock_conflicts_only(self):
        self.assertTrue(install.lock_error(100,'Could not get lock /var/lib/apt/lists/lock'))
        self.assertFalse(install.lock_error(100,'TLS certificate invalid'))
        self.assertFalse(install.lock_error(1,'Could not get lock'))
    def test_wait_timeout_and_release(self):
        states=iter([True,True,False])
        self.assertTrue(install.wait_idle(lambda:next(states),lambda _:None,attempts=3))
        self.assertFalse(install.wait_idle(lambda:True,lambda _:None,attempts=2))
    def run_batch(self, responses, specs=('chatgpt=2.0', 'claude-desktop=2.0')):
        calls = []
        installed = set()

        def runner(command):
            operation = ('refresh' if command[-1] == 'update' else
                         'download' if '--download-only' in command else 'install')
            selected = [spec for spec in specs if spec in command]
            calls.append((operation, selected, command))
            code, output = next(responses)
            if operation == 'install' and code == 0:
                installed.update(selected)
            return install.AptRun(code, output)

        result = install.upgrade(list(specs), runner, lambda _: ['bytedance-feishu-stable=7.0'],
                                 lambda spec: spec in installed)
        return result, calls

    def test_cancelled_refresh_does_not_install(self):
        result, calls = self.run_batch(iter([(130, '')]))
        self.assertEqual(result['code'], 130)
        self.assertEqual([x[0] for x in calls], ['refresh'])

    def test_success_keeps_one_interactive_install_and_pins_versions(self):
        result, calls = self.run_batch(iter([(0, ''), (0, ''), (0, '')]))
        self.assertEqual(result['code'], 0)
        self.assertEqual([x[0] for x in calls], ['refresh', 'download', 'install'])
        command = calls[-1][2]
        self.assertIn('chatgpt=2.0', command)
        self.assertIn('bytedance-feishu-stable=7.0', command)
        self.assertIn('--no-remove', command)
        self.assertIn('--no-download', command)
        self.assertNotIn('-y', command)
        self.assertNotIn('--assume-yes', command)

    def test_failed_download_cannot_block_already_downloaded_update(self):
        timeout = 'E: Failed to fetch https://example.test/claude.deb  Connection timed out'
        result, calls = self.run_batch(iter([
            (0, ''), (100, timeout), (0, ''), (0, ''), (100, timeout)]))
        self.assertEqual(result['updated'], ['chatgpt'])
        self.assertIn('超时', result['failures']['claude-desktop'])
        self.assertEqual([x[1] for x in calls if x[0] == 'install'], [['chatgpt=2.0']])

    def test_download_errors_are_localized_and_install_errors_stop(self):
        timeout = 'E：无法下载 https://example.test/app.deb  连接超时'
        result, calls = self.run_batch(iter([(0, ''), (100, timeout), (0, ''), (100, 'dpkg failed')]))
        self.assertEqual(result['updated'], [])
        self.assertIn('chatgpt', result['failures'])
        self.assertEqual(result['pending'], ['claude-desktop'])
        self.assertEqual(len(calls), 4)

    def test_failed_app_can_be_retried_after_download_recovers(self):
        timeout = 'E: Failed to fetch https://example.test/claude.deb  Connection timed out'
        first, _ = self.run_batch(iter([
            (0, ''), (100, timeout), (100, timeout), (0, ''), (0, '')]),
            specs=('claude-desktop=2.0', 'chatgpt=2.0'))
        self.assertEqual(first['updated'], ['chatgpt'])
        retry = tuple(name + '=2.0' for name in first['failures'])
        second, calls = self.run_batch(iter([(0, ''), (0, ''), (0, '')]), specs=retry)
        self.assertEqual(second['code'], 0)
        self.assertEqual(second['updated'], ['claude-desktop'])
        self.assertEqual(calls[-1][1], ['claude-desktop=2.0'])

    def test_exit_zero_without_version_change_is_not_success(self):
        replies = iter([install.AptRun(0, ''), install.AptRun(0, ''), install.AptRun(0, '')])
        result = install.upgrade(['code=2.0'], lambda _: next(replies), lambda _: [], lambda _: False)
        self.assertNotEqual(result['code'], 0)
        self.assertEqual(result['updated'], [])
        self.assertIn('code', result['failures'])

if __name__=='__main__': unittest.main()

class PromptTests(unittest.TestCase):
    def test_non_newline_prompt_is_forwarded_before_input(self):
        import subprocess, select, tempfile, os
        with tempfile.TemporaryDirectory() as cache:
            source = "import sys;sys.path.insert(0,"+repr(str(Path(__file__).resolve().parents[1]))+");import install;from pathlib import Path;install.backend.CACHE=Path("+repr(cache)+");install.packagekit_busy=lambda:False;sys.exit(install.apt_run(['/usr/bin/python3','-c',\"import sys;sys.stdout.write('Confirm?');sys.stdout.flush();sys.stdin.readline()\"]).code)"
            p=subprocess.Popen(['/usr/bin/python3','-c',source],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            try:
                ready=select.select([p.stdout],[],[],3)[0]
                self.assertTrue(ready,'confirmation prompt was buffered')
                self.assertEqual(os.read(p.stdout.fileno(),8),b'Confirm?')
                p.communicate(b'y\n',timeout=3)
                self.assertEqual(p.returncode,0)
            finally:
                if p.poll() is None:p.kill();p.communicate()
