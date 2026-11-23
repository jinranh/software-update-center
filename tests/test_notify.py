import json
import unittest
from unittest.mock import patch

import notify


class NotificationTests(unittest.TestCase):
    def test_report_includes_verified_vendor_candidates_when_system_refresh_fails(self):
        def run(command, timeout):
            args = command[2:]
            if args == ['--check']:
                raise RuntimeError('系统源暂不可达')
            if args == ['status']:
                return json.dumps({'apps': {
                    'vscode': {'channel': 'apt', 'installed': '1', 'uri': 'https://example.test/code'},
                    'claude': {'channel': 'apt', 'installed': '2', 'uri': 'https://example.test/claude'},
                    'chrome': {'channel': 'apt', 'installed': '2', 'error': '重复源，已阻止'},
                }})
            if args == ['check', 'vscode']:
                return json.dumps({'installed': '1', 'version': '2', 'held': False})
            if args == ['check', 'claude']:
                return json.dumps({'installed': '2', 'version': '3', 'held': True})
            self.fail(f'Unexpected command: {args}')

        with patch('notify.backend.run', side_effect=run):
            self.assertEqual([item['name'] for item in notify.collect_updates()],
                             ['Visual Studio Code'])

    def test_report_keeps_system_updates_when_vendor_refresh_fails(self):
        def run(command, timeout):
            if command[2:] == ['--check']:
                return json.dumps({'apps': [
                    {'name': '软件更新', 'available': True},
                    {'name': '已有最新版的应用', 'available': False},
                ]})
            raise RuntimeError('厂商源暂不可达')

        with patch('notify.backend.run', side_effect=run):
            self.assertEqual([item['name'] for item in notify.collect_updates()], ['软件更新'])
