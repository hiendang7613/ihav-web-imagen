# Changelog

## 0.3.0 (2026-10-08)

- Fixed: the page survey's sign-in matcher now matches whole words, so a chat title such as "Designing best eval tool" no longer marks a signed-in page as signed out.
- Fixed: when closing the browser also fails, the original error is still the one reported; the cleanup failure is added to its diagnostic. `headless.py login` prints any failure as JSON.
- `runtime.py login --site all` (or `--site <id>`) opens one tab per known web chat in the CloakBrowser profile for manual sign-in. Kimi opens at www.kimi.ai.
- `survey.py` records what each signed-in chat page offers (composer, buttons, file inputs, sign-in signs) into the state folder; `--diff` and `--diff-only` report what moved since the previous survey. It never types, clicks or sends.
- Provider cards for nine web chats under `references/providers/`. Each starts as "Adapter: not built; live: unverified".
- `gemini.py` (generate, resume, status) follows the same send-once receipt contract for Gemini. It is tested only against a scripted local page, has not run live, and the skill does not use it: ChatGPT is still the only adapter.
- Developer checks: offline tests can no longer hand over to an installed runtime; the real Claude Code and Codex installer tests run only with `IHAV_WEB_IMAGEN_HOST_TESTS=1`; CI also checks the survey matcher in Node.js 24.
- No new live image run for this release.

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

## 0.1.0 (2026-10-01)

- Plugin for Claude Code and Codex (one marketplace per host, `install.sh` for both).
- `ihav-web-imagen` skill: one line in, one request through your signed-in ChatGPT account, the original file saved in the current directory
  (or `--out DIR`); `--ref` (up to two references), `--dry-run`, `--verify-library`, `--json`.
- Safety: durable receipt before the click, read-only recovery, typed errors with `retry_safe`, explicit-invocation only.
- Offline suite of 250+ tests, including an image-check differential against Chromium and Pillow.
- `--browser chrome`: plain Google Chrome in a profile of its own (one-time sign-in with `headless.py login --browser chrome`), next to the default CloakBrowser; runs are resumed in the browser they were created with. Offline-tested only.
- Known limit: the browser runtime is not bundled yet.
