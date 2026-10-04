---
name: setup
description: One-time setup for ihav-web-imagen on this machine - a private venv with pinned packages, the CloakBrowser Chromium, and a ChatGPT sign-in done by the user. Sends no prompt. User-invoked as /ihav-web-imagen:setup.
disable-model-invocation: true
---
# ihav-web-imagen setup

Run, with the Bash tool's maximum timeout (the first Chromium download can take a few minutes):

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/core/ihav-web-imagen/scripts/runtime.py" setup
```

It creates a private venv in the plugin's state folder, installs the pinned packages and downloads the CloakBrowser Chromium once.
It sends nothing to ChatGPT. If it fails, show the last lines it printed; it needs Python 3.11 or newer.

Then the user signs in once. Only the user can do this: never ask for or type a password. Ask them to run this in the prompt
(the `!` runs it in this session and opens a browser window):

```
! python3 "${CLAUDE_PLUGIN_ROOT}/core/ihav-web-imagen/scripts/runtime.py" login
```

They sign in to ChatGPT in that window and close it. Then `runtime.py doctor` shows the state, and `/ihav-web-imagen:imagine a red fox`
draws the first image. For plain Google Chrome instead of CloakBrowser, use `login --browser chrome` and `--browser chrome` on each run.
