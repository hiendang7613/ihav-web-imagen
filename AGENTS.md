# Repository guidance

- The product and skill name is `ihav-web-imagen`; keep it consistent in manifests, trigger text, install examples and tests.
- The canonical agent instructions are `plugins/ihav-web-imagen/core/ihav-web-imagen/SKILL.md`; the Claude Code wrapper is `plugins/ihav-web-imagen/claude/skills/imagine/SKILL.md`.
- Preserve the rendered-UI-only boundary: never add API keys, private endpoints, cookie extraction, copied profiles or an automatic alternate provider.
- Never send an image request to test, demo or validate. Use `--dry-run`; tests are offline.
- Only a direct request to create or edit an image may send, once. After any uncertain send, observe or resume the same run; never submit it again.
- Before claiming library persistence, verify the matching library item; report unknown state honestly.
