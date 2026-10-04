# Changelog

## 0.2.0 (2026-10-04)

- Dedicated runtime for macOS and Linux, replacing the maintainer-specific environment with a vendored rendered-UI adapter.
- `runtime.py setup` creates a private environment with pinned dependencies and downloads CloakBrowser Chromium. `runtime.py login` opens manual sign-in; `runtime.py doctor` reports local installation state. None sends an image request.
- Separate state, profiles and receipts under the platform's application-data directory; override with `IHAV_WEB_IMAGEN_HOME`.
- Claude Code `/ihav-web-imagen:setup` runs no-send setup and gives the user a manual login command.
- README with beta quick start, both host invocations, browser/API comparison, receipt-backed historical cases, FAQ and ihav family links.
- Follows ChatGPT's 2026-10-04 UI: the Images library's "Create image" button, the Create image tool chosen from the composer's + menu, and the tool's inline pill kept when the prompt is typed (it was read as a draft, and filling the editor would have deleted it).
- Waits for the viewer's late Download button; `resume` saves the original from the owned turn's viewer first, with the library card only as a fallback. Neither path sends.
- One live run with this runtime on 2026-10-04 (macOS, maintainer's account): 1 Send, original saved by `resume`.
- Updated runtime and security documentation. The earlier live examples do not verify fresh-machine v0.2.0 generation, Linux generation, or Chrome generation.

## 0.1.0 (unreleased)

- Plugin for Claude Code and Codex (one marketplace per host, `install.sh` for both).
- `ihav-web-imagen` skill: one line in, one request through your signed-in ChatGPT account, the original file saved in the current directory
  (or `--out DIR`); `--ref` (up to two references), `--dry-run`, `--verify-library`, `--json`.
- Safety: durable receipt before the click, read-only recovery, typed errors with `retry_safe`, explicit-invocation only.
- Offline suite of 250+ tests, including an image-check differential against Chromium and Pillow.
- `--browser chrome`: plain Google Chrome in a profile of its own (one-time sign-in with `headless.py login --browser chrome`), next to the default CloakBrowser; runs are resumed in the browser they were created with. Offline-tested only.
- Known limit: the browser runtime is not bundled yet.
