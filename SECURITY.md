# Security policy

ihav-web-imagen drives a browser, so be precise about what it does and does not do.

## What it does

- Opens a dedicated, headless browser profile that **you** signed in to, and uses the normal ChatGPT Images page to send **one** request per run.
- Writes receipts (full prompt text, its hash, the state of the request) and reference snapshots under the runtime's local state directory, and saves
  the downloaded original where you point it (`--out`, default the current directory, never overwriting; a temporary `.chatgpt-original-*` file is made next to it).
- With `--browser chrome` it starts plain Google Chrome in its own `chrome-profile` under the state directory, never your everyday Chrome profile or a copy of it. You sign in yourself with `runtime.py login --browser chrome`.
- `runtime.py setup` creates a private Python environment, installs pinned dependencies and downloads CloakBrowser Chromium. `runtime.py login` opens a visible sign-in window. `runtime.py doctor` reads local installation state. None sends a prompt, and the dedicated runtime does not manage a background research worker.
- The one-line command uses the headless route only. The shared skill also documents manual routes (a connected browser tab, OpenCLI, the native Chrome
  app); those exist for a host that offers them and are gated by the host's own permissions and the user's request, never by this project.

## What it never does

- The browser stores its own authenticated session locally. The plugin does not collect passwords, extract cookies or tokens, call private ChatGPT endpoints, use an image API, copy a browser profile, or switch accounts
  or providers to get around a block, a rate limit or a policy message. It stops and reports a sign-in or verification challenge; it does not try to pass one.
- It does not run itself: the Claude Code image and setup skills are user-invoked only (`disable-model-invocation`), and the Codex image skill is explicit-invocation
  only (`allow_implicit_invocation: false`). Whether each host enforces those flags at runtime has not been tested end to end.

## Known exposure (read before installing)

- It spends your ChatGPT account (a generation counts against your plan) and automates the web UI, which ChatGPT's terms may restrict and which can lead to the
  account being limited or suspended. Unofficial; use an account you can afford to lose.
- Setup downloads third-party Python packages and CloakBrowser Chromium. The browser binary is not redistributed here; those dependencies have their own licenses and security boundaries. Offline tests do not prove their live behavior or fresh-machine compatibility.
- Prompts and reference images go to ChatGPT and remain in the local run folder for recovery. State lives in `~/Library/Application Support/ihav-web-imagen` on macOS or `${XDG_DATA_HOME:-~/.local/share}/ihav-web-imagen` on Linux, unless `IHAV_WEB_IMAGEN_HOME` overrides it. Protect this directory and the separate `~/.cloakbrowser` cache; never commit profiles, receipts or private references.
- Removing a local run folder removes its retained prompt and reference copies, but does not delete the ChatGPT conversation or Images library item. Preserve receipts until uncertain sends have been reconciled.
- Installing a plugin from a marketplace that tracks a branch runs whatever that branch contains inside your agent: read `install.sh` and the manifests first.

## Reporting a concern

Do not put prompts, private images, cookies, tokens or account details in a public issue. For a suspected vulnerability, use GitHub's private vulnerability
reporting once it is enabled for this repository; until then open an issue that contains no private data.
