# Contributing

Thanks for helping improve ihav-web-imagen.

## Principles

- Keep the ChatGPT rendered-UI-only boundary: no API keys, private endpoints, cookie extraction, profile copying or fallback provider.
- One request per run, and anything uncertain is recovered read-only, never repeated. Every error keeps its `error_kind` and `retry_safe`.
- A rule that rejects an input (an image check, a prompt check) needs evidence: see `tests/decoder_differential.json` in the skill for how.
- Keep claims measurable. No promises about latency, limits, exact dimensions or quality.
- **Never spend an account image request for a test, a demo or CI.** Use `--dry-run` and the offline suites.

## Before opening a change

1. `python3 scripts/validate_repo.py` and `python3 -m unittest discover -s tests -v` (portable; run in CI).
2. After `runtime.py setup`, also run the skill's suite from `plugins/ihav-web-imagen/core/ihav-web-imagen` with the private environment's `venv/bin/python -m unittest discover -s tests`. See [runtime layout](docs/how-it-works.md#what-is-on-disk) for the environment path. This suite does not send image requests.
3. Add or update an offline test for every behavioural change; describe anything that still needs a live signed-in test.

## Releases

Both plugin manifests carry the same `version` and hosts keep a cached copy until it changes: bump it in `plugins/ihav-web-imagen/.claude-plugin/plugin.json` and
`.codex-plugin/plugin.json` together with every user-visible change (a test checks they match), and record it in `CHANGELOG.md`.

## Pull requests

A focused title, the user-visible change, the host surface affected (Claude Code, Codex or both) and the validation you ran. No private prompts or images.
