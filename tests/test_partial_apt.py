import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import partial_apt


class PartialRefreshTests(unittest.TestCase):
    def test_failure_can_leave_a_fresh_usable_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            lists = Path(tmp) / 'lists'
            def update(command, timeout):
                self.assertIn(f'Dir::State::lists={lists}', command)
                self.assertIn('APT::Update::Error-Mode=any', command)
                (lists / 'healthy_stable_main_binary-amd64_Packages').write_text('test')
                raise RuntimeError('unrelated source offline')

            with patch('backend.run', side_effect=update):
                self.assertEqual(partial_apt.refresh_lists(lists), 'unrelated source offline')

    def test_never_uses_a_previous_snapshot_or_accepts_zero_indexes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stale = root / 'stale'
            stale.mkdir()
            with self.assertRaises(FileExistsError):
                partial_apt.refresh_lists(stale)
            with patch('backend.run', side_effect=RuntimeError('no source available')):
                with self.assertRaisesRegex(RuntimeError, 'no source available'):
                    partial_apt.refresh_lists(root / 'fresh')

class SourcePolicyTests(unittest.TestCase):
    def test_disabling_freshness_or_forcing_trust_excludes_the_origin(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'temporary.list'
            source.write_text(
                'deb [check-valid-until=no] https://expired.test stable main\n'
                'deb [trusted=yes enabled=no] https://forced.test stable main\n'
                'deb [check-date=true trusted=no] https://checked.test stable main\n'
            )
            with patch('official_apt.source_paths', return_value=[source]):
                self.assertEqual(partial_apt.weak_origins(),
                                 {'https://expired.test', 'https://forced.test'})

    def test_different_transport_cannot_bypass_a_weak_index(self):
        self.assertTrue(partial_apt.vendor.weakened_origin(
            'https://mirror.test/apt', {'http://mirror.test/apt'}))
        self.assertFalse(partial_apt.vendor.weakened_origin(
            'https://mirror.test/other', {'http://mirror.test/apt'}))
