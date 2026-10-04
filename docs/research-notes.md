# Source notes

This package follows the current native plugin layouts so one repository can expose the same workflow to Codex and Claude Code. The shared
skill stays under the plugin's `skills/` directory; host-specific manifests and marketplace catalogs provide discovery and invocation.

## Platform documentation

- [Claude Code plugin layout](https://code.claude.com/docs/en/plugins/create) documents the plugin root, `.claude-plugin/plugin.json`,
  `skills/<name>/SKILL.md`, namespaced slash commands, and session-only `--plugin-dir` loading.
- [Claude Code plugin manifest reference](https://code.claude.com/docs/en/plugins/manifest-reference) describes `skills` path entries and
  component path validation. The custom Claude skill path in this repository points to a small `imagine` wrapper around the shared workflow.
- [OpenAI plugin packaging](https://developers.openai.com/plugins/build/plugins) documents a root portable `plugin.json`, a shared `skills/`
  directory, the supported `.codex-plugin/plugin.json` compatibility manifest, and repo-local catalogs at `.agents/plugins/marketplace.json`.

The manifests are checked in independently because the two hosts have different catalog and invocation conventions. The skill itself remains
shared; the Claude wrapper is intentionally small and delegates to the same script and references as the Codex skill.

## Related implementation research

The detailed, commit-pinned review of 20 related repositories and two GitHub topic pages is preserved in
[`repository-findings.md`](../plugins/ihav-web-imagen/core/ihav-web-imagen/references/repository-findings.md), with commit identifiers and
source-file hashes in [`repository-sources.json`](../plugins/ihav-web-imagen/core/ihav-web-imagen/references/repository-sources.json). That
review is source inspection, not a popularity ranking or an end-to-end test. GitHub stars, install counts and trend claims are intentionally
not presented as product evidence.

The main design choices carried into this package are: explicit image requests, one submission per run, durable state before Send, read-only
recovery after uncertain submission, library identity separate from local export, no private endpoint or credential extraction, and clear
disclosure of which browser routes and systems have not been verified.

## Scope limits

Repository code can show that a tool uses a browser or a direct endpoint; it cannot prove that the user's ChatGPT account is signed in, that an
image was saved to that account, or that account quotas are unlimited. Those are separate live observations and are not inferred from names,
README claims or local tests.
