"""Run the production sign-in regex in JavaScript without importing the browser runtime.

This checks label matching only. The existing browser fixture owns DOM visibility
and inventory collection. Node is required; missing Node must fail rather than skip.
"""
from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SURVEY = ROOT / 'plugins/ihav-web-imagen/core/ihav-web-imagen/scripts/survey.py'
NODE_CHECK = """
const input = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
const matcher = require('node:vm').runInNewContext(input.literal, {}, {timeout: 1000});
if (Object.prototype.toString.call(matcher) !== '[object RegExp]') {
  throw new Error('The survey matcher must be a regular expression literal');
}
process.stdout.write(JSON.stringify(input.labels.map(label => matcher.test(label))));
"""


def matcher_literal(source: str) -> str:
    """Read the literal, not survey's imports or a Python translation of its regex."""
    values = [
        node.value for node in ast.parse(source).body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == 'INVENTORY' for target in node.targets)
    ]
    if len(values) != 1 or not isinstance(values[0], ast.Constant) or not isinstance(values[0].value, str):
        raise AssertionError('Expected exactly one literal INVENTORY assignment in survey.py')
    matches = re.findall(r'^\s*const words\s*=\s*(/[^\r\n]+/[a-z]*);\s*$', values[0].value, re.MULTILINE)
    if len(matches) != 1:
        raise AssertionError('Expected exactly one const words regex literal in INVENTORY')
    return matches[0]


class SurveyMatcherTests(unittest.TestCase):
    def test_production_signin_labels_without_substring_false_positives(self):
        node = shutil.which('node')
        self.assertIsNotNone(node, 'Node is required for the survey regression; CI installs Node 24')
        labels = [
            'Log in', 'Login', 'Sign in', 'Sign up', 'Continue with Google',
            'Designing best eval tool', 'Cataloging', 'Assign input',
            'How to sign in to Gmail',
        ]
        done = subprocess.run(
            [node, '-e', NODE_CHECK],
            input=json.dumps({'literal': matcher_literal(SURVEY.read_text(encoding='utf-8')), 'labels': labels}),
            capture_output=True, text=True, timeout=5, check=True,
        )
        results = json.loads(done.stdout)
        self.assertEqual(len(results), len(labels))
        # The final phrase is a known heuristic limitation, not proof of login state.
        expected = [True, True, True, True, True, False, False, False, True]
        for label, actual, wanted in zip(labels, results, expected):
            with self.subTest(label=label):
                self.assertIs(actual, wanted)

    def test_missing_or_ambiguous_matcher_fails_loudly(self):
        inventories = ['', 'const words = /one/i;\nconst words = /two/i;']
        for inventory in inventories:
            with self.subTest(inventory=inventory), self.assertRaisesRegex(AssertionError, 'exactly one const words'):
                matcher_literal('INVENTORY = ' + repr(inventory))


if __name__ == '__main__':
    unittest.main()
