import tempfile
from pathlib import Path
import unittest

import catalog
import official_apt


class CatalogTests(unittest.TestCase):
    def test_aliases_and_package_names_are_one_official_product(self):
        for query, app, publisher in (
            ('chat gpt', 'chatgpt', 'OpenAI'),
            ('ＣｈａｔＧＰＴ', 'chatgpt', 'OpenAI'),
            ('ChatGPT OpenAI', 'chatgpt', 'OpenAI'),
            ('微软 VS Code', 'vscode', 'Microsoft'),
            ('Claude', 'claude', 'Anthropic'),
            ('code', 'vscode', 'Microsoft'),
            ('谷歌浏览器', 'chrome', 'Google'),
            ('bytedance-feishu-stable', 'feishu', '北京飞书科技有限公司'),
        ):
            with self.subTest(query=query):
                self.assertEqual([(a.id, a.publisher) for a in catalog.search(query)],
                                 [(app, publisher)])
        self.assertEqual(catalog.search('chatgpt-community'), [])

    def test_platform_boundaries_on_jammy(self):
        platform = ('ubuntu', '22.04', 'amd64')
        self.assertEqual(catalog.APPS['chatgpt'].channel(*platform), 'unavailable')
        self.assertEqual(catalog.APPS['claude'].channel(*platform), 'apt')
        self.assertEqual(catalog.APPS['feishu'].channel(*platform), 'deb')
        self.assertEqual(catalog.APPS['docker'].channel(*platform), 'unavailable')
        self.assertEqual(catalog.APPS['chatgpt'].channel('ubuntu', '24.04', 'amd64'), 'deb')


class SourceTests(unittest.TestCase):
    def test_duplicate_and_unsigned_source_rejected(self):
        repo = catalog.APPS['vscode'].repo
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'a.list'
            path.write_text(f'deb [trusted=yes] {repo.uri} stable main\n')
            with self.assertRaisesRegex(RuntimeError, 'Signed-By|trusted'):
                official_apt.configured_source(repo, [path], 'amd64')
            path.write_text(f'deb [signed-by=/usr/share/keyrings/microsoft.gpg] {repo.uri} stable main\n')
            other = path.with_name('b.sources')
            other.write_text(f'Types: deb\nURIs: {repo.uri}/\nSuites: stable\n'
                             'Components: main\nSigned-By: /usr/share/keyrings/microsoft.gpg\n')
            with self.assertRaisesRegex(RuntimeError, '重复'):
                official_apt.configured_source(repo, [path, other], 'amd64')

    def test_package_uri_matches_full_repository_path(self):
        repo = catalog.APPS['vscode'].repo
        prefix = 'https://packages.microsoft.com/repos/'
        self.assertTrue(repo.owns(prefix + 'code/pool/main/c/code.deb'))
        self.assertFalse(repo.owns(prefix + 'code-fake/pool/main/c/code.deb'))
        self.assertFalse(repo.owns('https://packages.microsoft.com.evil.test/repos/code/code.deb'))


class TransactionTests(unittest.TestCase):
    def test_preview_reads_apt_temporary_dependency_annotations(self):
        output = ('Inst humanity-icon-theme (0.6.16 Ubuntu:22.04/jammy [all]) []\n'
                  'Inst code [1] (2 stable [amd64]) [code:amd64 ]\n'
                  'Conf humanity-icon-theme (0.6.16 Ubuntu:22.04/jammy [all])\n'
                  'Conf code (2 stable [amd64])\n')
        self.assertEqual(official_apt.changes_from_simulation(output), [
            ('humanity-icon-theme', None, '0.6.16', 'all'), ('code', '1', '2', 'amd64'),
        ])

    def test_dpkg_hook_rejects_change_after_the_review(self):
        import io
        import json
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            archive = directory / 'code_2_amd64.deb'
            archive.write_bytes(b'example bytes')
            plan = directory / 'plan.json'
            plan.write_text(json.dumps([dict(package='code', arch='amd64', previous='1',
                                            version='2', file=archive.name,
                                            sha256=official_apt.sha256_file(archive))]))
            header = 'VERSION 3\nconfig=test\n\n'
            line = f'code 1 amd64 none < 2 amd64 none {archive}\n'
            official_apt.verify_dpkg_plan(plan, io.StringIO(header + line))
            with self.assertRaisesRegex(RuntimeError, '已变化'):
                official_apt.verify_dpkg_plan(plan, io.StringIO(header + line.replace('1 amd64', '3 amd64')))

    def test_preview_rejects_pending_unrelated_configuration(self):
        simulation = (
            'Inst code (2 stable [amd64])\n'
            'Conf code (2 stable [amd64])\n'
            'Conf unrelated (5 stable [amd64])\n'
        )
        with self.assertRaisesRegex(RuntimeError, 'unrelated'):
            official_apt.changes_from_simulation(simulation)

    def test_same_mirror_dependency_in_updates_and_security_is_unambiguous(self):
        from unittest.mock import patch
        app = catalog.APPS['vscode']
        uri = 'https://mirror.example/ubuntu/pool/dependency.deb'
        checksum = 'a' * 64
        record = ('dependency', '', '1', 'all', checksum, True, 'https://mirror.example/ubuntu')
        with tempfile.TemporaryDirectory() as tmp:
            session = official_apt.Session.__new__(official_apt.Session)
            session.scratch = Path(tmp)
            session.app = app
            session.weakened = set()
            with patch('official_apt.backend.run', return_value=f"'{uri}' dependency.deb 15 \n"):
                manifest = session.manifest([], [('dependency', None, '1', 'all')], {
                    uri: {(*record, 'jammy-updates_Packages'), (*record, 'jammy-security_Packages')}
                })
        self.assertEqual(len(manifest), 1)
        self.assertEqual(manifest[0]['sha256'], checksum)
