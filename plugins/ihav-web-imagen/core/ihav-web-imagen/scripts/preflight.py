#!/usr/bin/env python3
"""No-send OpenCLI connection check; no browser actions or daemon startup."""

import argparse
import json
import os
import re
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

STATUS_URL = "http://127.0.0.1:19825/status"
MAX_STATUS_BYTES = 32768
BRIDGE_TIMEOUT = 1.5


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def cli_version(binary):
    """Read npm package metadata without executing the CLI/update checker."""
    if not binary:
        return None
    try:
        for parent in list(Path(binary).resolve().parents)[:6]:
            package = parent / "package.json"
            if package.is_file():
                data = json.loads(package.read_text(encoding="utf-8"))
                if isinstance(data, dict) and data.get("name") == "@jackwener/opencli":
                    version = data.get("version")
                    return version if isinstance(version, str) else None
    except (OSError, ValueError, TypeError):
        return None
    return None


def read_bridge(context_id=None):
    query = urllib.parse.urlencode({"contextId": context_id}) if context_id else ""
    request = urllib.request.Request(
        STATUS_URL + ("?" + query if query else ""),
        headers={"X-OpenCLI": "1"}, method="GET",
    )
    # Never send loopback status through a system proxy or follow a redirect.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(request, timeout=BRIDGE_TIMEOUT) as response:
        raw = response.read(MAX_STATUS_BYTES + 1)
    if len(raw) > MAX_STATUS_BYTES:
        raise ValueError("bridge response too large")
    return json.loads(raw)


def classify(version, status, context_id=None):
    if not re.fullmatch(r"1\.8\.\d+", version or ""):
        return "unsupported_cli_version"
    if not isinstance(status, dict) or status.get("ok") is not True:
        return "bridge_protocol_unknown"
    flags = ("extensionConnected", "profileRequired", "profileDisconnected")
    if any(type(status.get(key)) is not bool for key in flags):
        return "bridge_protocol_unknown"
    if status.get("daemonVersion") != version:
        return "cli_daemon_mismatch"
    profiles = status.get("profiles")
    if not isinstance(profiles, list) or any(not isinstance(p, dict) for p in profiles):
        return "bridge_protocol_unknown"
    if status["profileDisconnected"]:
        return "selected_profile_disconnected"
    if status["profileRequired"] or (len(profiles) > 1 and not context_id):
        return "profile_selection_required"
    if not status["extensionConnected"]:
        return "extension_disconnected"
    selected = status.get("contextId")
    if not isinstance(selected, str) or not selected:
        return "bridge_protocol_unknown"
    if context_id and selected != context_id:
        return "selected_profile_mismatch"
    matching = [p for p in profiles if p.get("contextId") == selected
                and p.get("extensionConnected") is True]
    if len(matching) != 1:
        return "bridge_protocol_unknown"
    return "bridge_advertises_connected"


def probe(context_id=None):
    started = time.monotonic()
    binary = shutil.which("opencli")
    version = cli_version(binary)
    status = None
    daemon_port = os.environ.get("OPENCLI_DAEMON_PORT", "").strip()
    if not binary:
        state = "cli_missing"
    elif not re.fullmatch(r"1\.8\.\d+", version or ""):
        state = "unsupported_cli_version"
    elif daemon_port and daemon_port != "19825":
        state = "unsupported_daemon_port"
    else:
        try:
            status = read_bridge(context_id)
        except urllib.error.HTTPError:
            state = "bridge_http_error"
        except (urllib.error.URLError, OSError, TimeoutError):
            state = "bridge_unreachable"
        except (ValueError, TypeError):
            state = "bridge_protocol_unknown"
        else:
            state = classify(version, status, context_id)
    profiles = status.get("profiles") if isinstance(status, dict) else None
    return {
        "schema_version": 1,
        "mode": "no_send",
        "cli_installed": bool(binary),
        "cli_version": version,
        "bridge_state": state,
        "connected_profile_count": len(profiles) if isinstance(profiles, list) else None,
        "explicit_profile_selection": bool(context_id),
        "ready": state == "bridge_advertises_connected",
        "chatgpt_session_verified": False,
        "image_capability_verified": False,
        "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context-id", help="Exact context ID from opencli profile list; no aliases")
    args = parser.parse_args()
    context_id = args.context_id
    if context_id is not None and (not context_id.strip() or len(context_id) > 256):
        parser.error("--context-id must be nonempty and at most 256 characters")
    result = probe(context_id.strip() if context_id else None)
    print(json.dumps(result, ensure_ascii=True))
    return 0 if result["ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
