from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("validate_repo", ROOT / "scripts/validate_repo.py")
assert SPEC and SPEC.loader
validate_repo = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validate_repo)


class RepositoryContractTests(unittest.TestCase):
    def test_repository_contract_is_valid(self):
        self.assertEqual(validate_repo.check(), [])

    def test_all_manifests_use_the_requested_name(self):
        paths = [
            ROOT / "plugins/ihav-web-imagen/.claude-plugin/plugin.json",
            ROOT / "plugins/ihav-web-imagen/.codex-plugin/plugin.json",
        ]
        for path in paths:
            with self.subTest(path=path):
                self.assertEqual(validate_repo.read_json(path, [])["name"], "ihav-web-imagen")

    def test_codex_and_claude_catalogs_point_to_the_same_plugin(self):
        claude = validate_repo.read_json(ROOT / ".claude-plugin/marketplace.json", [])
        codex = validate_repo.read_json(ROOT / ".agents/plugins/marketplace.json", [])
        self.assertEqual(claude["plugins"][0]["source"], "./plugins/ihav-web-imagen")
        self.assertEqual(codex["plugins"][0]["source"]["path"], "./plugins/ihav-web-imagen")


if __name__ == "__main__":
    unittest.main()
