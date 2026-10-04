"""Local contract tests; no browser, provider, subprocess, or real network."""

import importlib.util
import io
import json
import os
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "preflight.py"
SPEC = importlib.util.spec_from_file_location("images_preflight", SCRIPT)
preflight = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(preflight)


def connected_status():
    return {"ok": True, "daemonVersion": "1.8.7", "extensionConnected": True,
            "profileRequired": False, "profileDisconnected": False,
            "contextId": "profile-a", "profiles": [
                {"contextId": "profile-a", "extensionConnected": True}]}


class PreflightTests(unittest.TestCase):
    def run_probe(self, status=None, error=None, version="1.8.7", binary="/fake/opencli", context=None):
        with patch.object(preflight.shutil, "which", return_value=binary), \
             patch.object(preflight, "cli_version", return_value=version), \
             patch.object(preflight, "read_bridge", return_value=status, side_effect=error) as read, \
             patch.dict(os.environ, {"OPENCLI_DAEMON_PORT": ""}):
            return preflight.probe(context), read.call_count

    def test_connected_is_not_chatgpt_or_image_verification(self):
        result, calls = self.run_probe(connected_status())
        self.assertTrue(result["ready"])
        self.assertEqual(calls, 1)
        self.assertFalse(result["chatgpt_session_verified"])
        self.assertFalse(result["image_capability_verified"])
        self.assertEqual(result["mode"], "no_send")

    def test_missing_cli_and_unknown_version_make_no_network_request(self):
        for version, binary, expected in [(None, None, "cli_missing"),
                                          ("1.9.0", "/fake", "unsupported_cli_version")]:
            with self.subTest(expected=expected):
                result, calls = self.run_probe(version=version, binary=binary)
                self.assertEqual(result["bridge_state"], expected)
                self.assertFalse(result["ready"])
                self.assertEqual(calls, 0)

    def test_disconnected_extension_fails_fast_once(self):
        status = connected_status()
        status.update(extensionConnected=False, contextId=None, profiles=[])
        result, calls = self.run_probe(status)
        self.assertEqual(result["bridge_state"], "extension_disconnected")
        self.assertFalse(result["ready"])
        self.assertEqual(calls, 1)

    def test_multiple_profiles_require_selection(self):
        status = connected_status()
        status["profiles"].append({"contextId": "profile-b", "extensionConnected": True})
        result, _ = self.run_probe(status)
        self.assertEqual(result["bridge_state"], "profile_selection_required")
        selected, _ = self.run_probe(status, context="profile-a")
        self.assertTrue(selected["ready"])
        mismatched, _ = self.run_probe(status, context="profile-b")
        self.assertEqual(mismatched["bridge_state"], "selected_profile_mismatch")

    def test_protocol_drift_and_mismatches_never_report_ready(self):
        for change, expected in [({"daemonVersion": "1.8.6"}, "cli_daemon_mismatch"),
                                 ({"extensionConnected": "true"}, "bridge_protocol_unknown"),
                                 ({"profiles": {}}, "bridge_protocol_unknown"),
                                 ({"profileDisconnected": True}, "selected_profile_disconnected"),
                                 ({"ok": False}, "bridge_protocol_unknown")]:
            with self.subTest(change=change):
                status = connected_status()
                status.update(change)
                result, _ = self.run_probe(status)
                self.assertEqual(result["bridge_state"], expected)
                self.assertFalse(result["ready"])

    def test_network_or_bad_json_failure_is_not_retried(self):
        for error, expected in [(urllib.error.URLError("offline"), "bridge_unreachable"),
                                (ValueError("bad JSON"), "bridge_protocol_unknown")]:
            result, calls = self.run_probe(error=error)
            self.assertEqual(result["bridge_state"], expected)
            self.assertEqual(calls, 1)

    def test_output_does_not_leak_raw_bridge_data(self):
        status = connected_status()
        status.update(sessionLeases=[{"prompt": "private text"}], cookies="secret")
        status["profiles"][0]["email"] = "private@example.com"
        result, _ = self.run_probe(status, context="profile-a")
        output = json.dumps(result)
        for private in ("private text", "secret", "private@example.com", "profile-a", "sessionLeases"):
            self.assertNotIn(private, output)

    def test_request_is_bounded_loopback_get_with_no_redirects_or_proxy(self):
        with patch.object(preflight.urllib.request, "build_opener") as build:
            opener = build.return_value
            opener.open.return_value = io.BytesIO(json.dumps(connected_status()).encode())
            result = preflight.read_bridge("a &b")
            request = opener.open.call_args.args[0]
            self.assertEqual(request.full_url, "http://127.0.0.1:19825/status?contextId=a+%26b")
            self.assertEqual(request.get_method(), "GET")
            self.assertEqual(opener.open.call_args.kwargs["timeout"], 1.5)
            self.assertEqual(build.call_args.args[0].proxies, {})
            self.assertIsInstance(build.call_args.args[1], preflight.NoRedirect)
            self.assertEqual(result["contextId"], "profile-a")

    def test_help_has_no_connection_side_effect(self):
        with patch("sys.argv", [str(SCRIPT), "--help"]), \
             patch.object(preflight, "probe") as probe, \
             patch("sys.stdout", new_callable=io.StringIO):
            with self.assertRaises(SystemExit) as ended:
                preflight.main()
            self.assertEqual(ended.exception.code, 0)
            probe.assert_not_called()

    def test_cli_exit_code_reflects_blocker_and_ready(self):
        for ready, code in [(False, 2), (True, 0)]:
            with patch("sys.argv", [str(SCRIPT)]), \
                 patch.object(preflight, "probe", return_value={"ready": ready}), \
                 patch("sys.stdout", new_callable=io.StringIO) as output:
                self.assertEqual(preflight.main(), code)
                self.assertEqual(json.loads(output.getvalue()), {"ready": ready})


if __name__ == "__main__":
    unittest.main()
