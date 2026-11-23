from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import catalog
import official_apt


class ChromeRepairTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.sources = self.root / 'sources.list.d'
        self.sources.mkdir()
        self.legacy = self.sources / 'google-chrome.list'
        self.canonical = self.sources / 'google-chrome.sources'
        self.legacy.write_text(
            'deb [signed-by=/usr/share/keyrings/google-chrome.gpg] '
            'http://dl.google.com/linux/chrome/deb/ stable main\n')
        self.app = catalog.APPS['chrome']
        self.key = self.root / 'chrome.asc'
        self.key.write_text('test key snapshot')
        self.canonical.write_text(official_apt.repo_stanza(
            self.app.repo, self.app.repo.uri, self.key, 'amd64'))
        self.scratch = self.root / 'scratch'
        self.scratch.mkdir()
        overrides = patch.multiple(official_apt, SOURCE_PARTS=self.sources,
                                   SOURCE_LIST=self.root / 'sources.list')
        overrides.start()
        self.addCleanup(overrides.stop)

    def repair(self, expected=None):
        return official_apt.repair_chrome(self.app, self.scratch, 'amd64', expected)

    def checked_source(self, mock):
        mock.return_value.check_version.return_value = {'version': '158.0'}
        mock.return_value.source = next(official_apt.source_entries(self.canonical))
        mock.return_value.key_path = self.key
        mock.return_value.uri = self.app.repo.uri
        mock.return_value.repo = self.app.repo

    def test_preview_omits_only_legacy_and_apply_preserves_its_bytes(self):
        old, retained = self.legacy.read_bytes(), self.canonical.read_bytes()
        with patch('official_apt.Session') as session:
            self.checked_source(session)
            preview = self.repair()
            self.assertEqual(preview['legacy'], str(self.legacy))
            self.assertEqual(preview['canonical'], str(self.canonical))
            self.assertEqual(preview['version'], '158.0')
            backup = Path(preview['backup'])
            self.assertEqual(self.legacy.read_bytes(), old)
            self.assertFalse(backup.exists())
            self.assertEqual(session.call_args.kwargs['paths'], [self.canonical])
            self.repair(official_apt.plan_digest(preview))
        self.assertFalse(self.legacy.exists())
        self.assertEqual(backup.read_bytes(), old)
        self.assertEqual(self.canonical.read_bytes(), retained)

    def test_mixed_legacy_file_is_never_eligible(self):
        with self.legacy.open('a') as stream:
            stream.write('deb https://example.org/ubuntu jammy main\n')
        with self.assertRaisesRegex(RuntimeError, '单一条目'):
            self.repair()

    def test_additional_duplicate_keeps_all_sources_active(self):
        (self.sources / 'extra.list').write_bytes(self.legacy.read_bytes())
        with self.assertRaisesRegex(RuntimeError, 'extra.list'):
            self.repair()
        self.assertTrue(self.legacy.exists())

    def test_existing_backup_is_preserved(self):
        backup = self.legacy.with_name('google-chrome.list.software-update-center.disabled')
        backup.write_text('earlier backup')
        with self.assertRaisesRegex(RuntimeError, '备份已存在'):
            self.repair()
        self.assertEqual(backup.read_text(), 'earlier backup')
        self.assertTrue(self.legacy.exists())

    def test_canonical_symlink_is_not_eligible(self):
        redirected = self.root / 'redirected.sources'
        self.canonical.rename(redirected)
        self.canonical.symlink_to(redirected)
        with self.assertRaisesRegex(RuntimeError, '需要独立'):
            self.repair()
        self.assertTrue(self.legacy.exists())

    def test_failed_authentication_keeps_legacy_source_active(self):
        with patch('official_apt.Session') as session:
            self.checked_source(session)
            preview = self.repair()
            session.side_effect = RuntimeError('厂商源签名验证失败')
            with self.assertRaisesRegex(RuntimeError, '签名验证失败'):
                self.repair(official_apt.plan_digest(preview))
        self.assertTrue(self.legacy.exists())
        self.assertFalse(Path(preview['backup']).exists())

    def test_key_changed_since_snapshot_blocks_apply(self):
        with patch('official_apt.Session') as session:
            self.checked_source(session)
            self.scratch.joinpath('verified.asc').write_bytes(self.key.read_bytes())
            session.return_value.key_path = self.scratch / 'verified.asc'
            preview = self.repair()
            self.key.write_text('different key material')
            with self.assertRaisesRegex(RuntimeError, '签名密钥已变化'):
                self.repair(official_apt.plan_digest(preview))
        self.assertTrue(self.legacy.exists())
        self.assertFalse(Path(preview['backup']).exists())

    def test_changed_source_requires_a_new_preview(self):
        with patch('official_apt.Session') as session:
            self.checked_source(session)
            preview = self.repair()
            with self.canonical.open('a') as stream:
                stream.write('# changed while the dialog was open\n')
            with self.assertRaisesRegex(RuntimeError, '重新审查'):
                self.repair(official_apt.plan_digest(preview))
        self.assertTrue(self.legacy.exists())
        self.assertFalse(Path(preview['backup']).exists())
