# Contributing

Thanks for helping improve ihav-web-imagen.

## Principles

- Keep the ChatGPT rendered-UI-only boundary: no API keys, private endpoints, cookie extraction, profile copying or fallback provider.
- One request per run, and anything uncertain is recovered read-only, never repeated. Every error keeps its `error_kind` and `retry_safe`.
- A rule that rejects an input (an image check, a prompt check) needs evidence: see `tests/decoder_differential.json` in the skill for how.
- Keep claims measurable. No promises about latency, limits, exact dimensions or quality.
- **Never spend an account image request for a test, a demo or CI.** Use `--dry-run` and the offline suites.

## Before opening a change

Use Python 3.11 or newer and Node.js 24 for the portable checks below. The root
tests evaluate the production survey matcher in Node.js; they do not need the
browser runtime. CI uses Node.js 24 with Python 3.11 and 3.12.

1. `python3 scripts/validate_repo.py` and `python3 -m unittest discover -s tests -v` (portable; run in CI).
2. After `runtime.py setup`, also run the skill's suite from `plugins/ihav-web-imagen/core/ihav-web-imagen` with the private environment's `venv/bin/python -m unittest discover -s tests`. Set `IHAV_WEB_IMAGEN_HOME` to a new fixture directory for this command. Its `config.json` should contain only `browser_binary`, pointing to the existing Chromium executable. Do not copy the signed-in profile or other account settings. See [runtime layout](docs/how-it-works.md#what-is-on-disk) for the environment path. This suite does not send image requests.
3. Add or update an offline test for every behavioural change; describe anything that still needs a live signed-in test.

The root suite skips two native installer integration tests by default, even when
`claude` or `codex` is installed. Set `IHAV_WEB_IMAGEN_HOST_TESTS=1` only when you
intend to run them. They call each available host's real plugin install/update
commands in throwaway homes; they are separate from the portable checks.

## Releases

Both plugin manifests carry the same `version` and hosts keep a cached copy until it changes: bump it in `plugins/ihav-web-imagen/.claude-plugin/plugin.json` and
`.codex-plugin/plugin.json` together with every user-visible change (a test checks they match), and record it in `CHANGELOG.md`.

## Pull requests

A focused title, the user-visible change, the host surface affected (Claude Code, Codex or both) and the validation you ran. No private prompts or images.
