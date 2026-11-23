import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import backend as b

def pkg(name='code', installed='1.0', candidate='2.0', downloadable=True, held=False):
    return NS(name=name, is_installed=True, installed=NS(version=installed),
              candidate=NS(version=candidate, downloadable=downloadable),
              _pkg=NS(selected_state=2 if held else 1))

class SelectionTests(unittest.TestCase):
    def test_only_repository_apps_are_selectable(self):
        self.assertTrue(b.app_record(pkg(), 'VS Code')['available'])
        self.assertIsNone(b.app_record(pkg(downloadable=False), 'Local'))
    def test_excluded_and_held_never_selectable(self):
        for name in ('docker-desktop', 'bytedance-feishu-stable', 'workbuddy', 'mongodb-compass'):
            self.assertIsNone(b.app_record(pkg(name=name), name))
        self.assertFalse(b.app_record(pkg(held=True), 'Held')['available'])
    def test_no_downgrade(self):
        self.assertFalse(b.app_record(pkg(installed='3.0'), 'Newer')['available'])
    def test_pin_selection_and_reject_invalid(self):
        rows=[b.app_record(pkg(), 'Code')]
        self.assertEqual(b.selected_specs(rows,['code']), ['code=2.0'])
        for names in ([], ['missing'], ['code','code']):
            with self.assertRaises(ValueError): b.selected_specs(rows,names)
        rows[0]['available']=False
        with self.assertRaises(ValueError): b.selected_specs(rows,['code'])
    def test_simulation_refuses_removals(self):
        with self.assertRaises(RuntimeError): b.validate_plan('Remv existing [1.0]\nInst code [1.0] (2.0 repo [amd64])')
        with self.assertRaises(RuntimeError): b.validate_plan('Inst bytedance-feishu-stable [1] (2 repo [amd64])')
        self.assertEqual(b.validate_plan('Inst code [1.0] (2.0 repo [amd64])'), ['code'])
        with self.assertRaises(RuntimeError): b.validate_plan('0 upgraded, 0 newly installed')
    def test_installer_rejects_options_or_unpinned_packages(self):
        self.assertEqual(b.validate_specs(['code=2.0-1']), ['code=2.0-1'])
        for values in ([], ['--allow-unauthenticated'], ['code'], ['code='], ['docker-desktop=1'], ['code=2;touch /tmp/x']):
            with self.assertRaises(ValueError): b.validate_specs(values)

if __name__=='__main__': unittest.main()
