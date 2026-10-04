#!/usr/bin/env python3
"""Validate ihav-web-imagen's portable plugin metadata and local references."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins/ihav-web-imagen"
SKILL = PLUGIN / "core/ihav-web-imagen/SKILL.md"


def read_json(path: Path, errors: list[str]):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        errors.append(f"missing required file: {path.relative_to(ROOT)}")
    except (UnicodeError, json.JSONDecodeError) as exc:
        errors.append(f"invalid JSON in {path.relative_to(ROOT)}: {exc}")
    return None


def repo_files() -> list[Path]:
    """The files this repository ships: tracked plus untracked-but-not-ignored (local room state is excluded)."""
    try:
        listed = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT,
                                capture_output=True, check=True).stdout.decode().split("\0")
        return [ROOT / name for name in listed if name and (ROOT / name).is_file()]
    except (OSError, subprocess.CalledProcessError):
        return [path for path in ROOT.rglob("*") if path.is_file() and ".git" not in path.parts]


def check() -> list[str]:
    errors: list[str] = []
    manifests = [
        read_json(PLUGIN / ".claude-plugin/plugin.json", errors),
        read_json(PLUGIN / ".codex-plugin/plugin.json", errors),
    ]
    for index, manifest in enumerate(manifests):
        if manifest is not None and manifest.get("name") != "ihav-web-imagen":
            errors.append(f"manifest {index + 1} must use the name ihav-web-imagen")

    claude_market = read_json(ROOT / ".claude-plugin/marketplace.json", errors)
    if claude_market is not None:
        if claude_market.get("name") != "ihav-web-imagen":
            errors.append("Claude marketplace name must be ihav-web-imagen")
        entries = claude_market.get("plugins", [])
        if not any(entry.get("name") == "ihav-web-imagen" and entry.get("source") == "./plugins/ihav-web-imagen"
                   for entry in entries if isinstance(entry, dict)):
            errors.append("Claude marketplace must point to ./plugins/ihav-web-imagen")

    codex_market = read_json(ROOT / ".agents/plugins/marketplace.json", errors)
    if codex_market is not None:
        entries = codex_market.get("plugins", [])
        if not any(entry.get("name") == "ihav-web-imagen"
                   and entry.get("source", {}).get("path") == "./plugins/ihav-web-imagen"
                   for entry in entries if isinstance(entry, dict) and isinstance(entry.get("source"), dict)):
            errors.append("Codex marketplace must point to ./plugins/ihav-web-imagen")

    if SKILL.is_file():
        text = SKILL.read_text(encoding="utf-8")
        if not text.startswith("---\n"):
            errors.append("skill must start with YAML frontmatter")
        else:
            frontmatter = text.split("---", 2)[1]
            if not re.search(r"(?m)^name:\s*ihav-web-imagen\s*$", frontmatter):
                errors.append("skill frontmatter name must be ihav-web-imagen")
            if not re.search(r"(?m)^description:\s*\S", frontmatter):
                errors.append("skill frontmatter needs a non-empty description")
        if len(text.splitlines()) > 500:
            errors.append("main SKILL.md must stay under 500 lines")

    for path in repo_files():
        if any(part in {".git", "__pycache__"} for part in path.parts):
            continue
        if path.suffix.lower() in {".md", ".json", ".toml", ".yml", ".yaml"}:      # not .py: code builds Markdown links at run time
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeError:
                errors.append(f"not UTF-8 text: {path.relative_to(ROOT)}")
                continue
            for raw_target in re.findall(r"\]\(([^)]+)\)", text):
                target = raw_target.strip()
                local_target = target.split("#", 1)[0]
                if target.startswith(("https://", "http://", "mailto:", "#")) or not local_target:
                    continue
                local = (path.parent / local_target).resolve()
                if not local.exists():
                    errors.append(f"broken local link in {path.relative_to(ROOT)}: {target}")

    private = ("/Users/", "chatgpt-images-ui", "gpt-web-imagen", "CLAUDE_0", "CODEX_0")      # a user path, old names, internal agent names
    for path in repo_files():
        if (any(part in {".git", "__pycache__"} for part in path.parts)
                or path.suffix.lower() not in {".md", ".json", ".py", ".yml", ".yaml", ".sh", ".svg", ".toml"}
                or path == Path(__file__).resolve() or path.name in {"test_repo.py", "test_install.py"}):
            continue
        text = path.read_text(encoding="utf-8")
        for needle in private:
            if needle in text:
                errors.append(f"private or old-name text '{needle}' in {path.relative_to(ROOT)}")
    return errors


def main() -> int:
    errors = check()
    if errors:
        print("Repository validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print("Repository validation passed: names, manifests, skill, and local links are consistent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
