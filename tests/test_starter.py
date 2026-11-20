import json
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'workbench/workbench.py'
VAULT = ROOT / 'examples/vault'


class StarterTests(unittest.TestCase):
    def call(self, *args, vault=VAULT):
        prefix = ['--vault', str(vault)] if vault is not None else []
        return subprocess.run([sys.executable, '-I', '-B', str(APP), *prefix, *args],
                              cwd=ROOT, capture_output=True, text=True, timeout=8)

    def check_json(self, *args):
        proc = self.call(*args)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)

    def test_cli_requires_an_explicit_vault(self):
        result = self.call('health', vault=None)
        self.assertEqual(result.returncode, 2)
        self.assertIn('--vault', result.stderr)

    def test_documented_demo_retrieval_and_evidence(self):
        hit = self.check_json('search', 'Paper Lantern', '--limit', '5')['hits'][0]
        self.assertEqual(hit['path'], 'Projects/Lantern Exhibit.md')
        excerpt = self.check_json('read', hit['path'], '--line', str(hit['line']),
                                  '--sha256', hit['sha256'], '--chars', '4500')
        self.assertIn('folded paper models', excerpt['text'])
        self.assertEqual(excerpt['sha256'], hit['sha256'])
        self.assertEqual(self.call('read', hit['path'], '--sha256', '0' * 64).returncode, 2)

    def test_documented_demo_path_and_health(self):
        route = self.check_json('path', 'BRAIN-MAP.md', 'Decisions/Lighting Review.md')
        self.assertEqual(route['hops'], 1)
        h = self.check_json('health')
        self.assertEqual(h['index']['notes'], 4)
        self.assertFalse(h['index']['drift'])
        self.assertEqual(h['graph']['detected_missing'], 0)
        self.assertEqual(h['graph']['unreachable'], 0)
        self.assertEqual(h['maintenance']['status'], 'not configured')
