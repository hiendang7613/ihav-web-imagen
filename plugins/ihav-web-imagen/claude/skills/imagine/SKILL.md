---
name: imagine
description: Draw one image from a description with your own signed-in ChatGPT account (headless, original file saved to disk). User-invoked as /ihav-web-imagen:imagine <what to draw>; options --ref IMAGE (up to two), --out DIR, --dry-run.
disable-model-invocation: true
argument-hint: <what to draw> [--ref IMAGE] [--out DIR] [--dry-run]
---
# ihav-web-imagen

User-invoked: `/ihav-web-imagen:imagine <what to draw>`, for example `/ihav-web-imagen:imagine a red fox in watercolor`. The description is in the
ARGUMENTS line below. The skill and its scripts live at `${CLAUDE_PLUGIN_ROOT}/core/ihav-web-imagen/` (read its `SKILL.md` and `references/`
for anything beyond this command).

Run exactly one command, with the Bash tool's maximum timeout (600000 ms; the image takes 1-3 minutes and the default wait is 8 minutes):

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/core/ihav-web-imagen/scripts/imagine.py" - <<'TEXT'
<the description, unchanged>
TEXT
```

The quoted heredoc keeps `$`, backticks, backslashes and quotes exactly as typed (a double-quoted argument would let the shell alter them).
Options go after the `-`, for example `imagine.py - --ref photo.png <<'TEXT'`.

- If it says the browser runtime is not set up, run `python3 "${CLAUDE_PLUGIN_ROOT}/core/ihav-web-imagen/scripts/runtime.py" setup` once (it sends
  nothing), then ask the user to sign in once with `! python3 "${CLAUDE_PLUGIN_ROOT}/core/ihav-web-imagen/scripts/runtime.py" login` and run the
  drawing command again only after they say they are signed in.
- Use the description exactly as the user wrote it: do not embellish it and do not ask questions about an ordinary description. If ARGUMENTS
  ends with options (`--ref photo.png`, `--out DIR`, `--name STEM`, `--browser chrome`, `--verify-library`, `--wait-seconds N`, `--json`, `--dry-run`), pass
  those as options and the rest as the description.
- It sends ONE request through the headless profile and saves the original file in the current directory, or in the folder you choose with
  `--out DIR` (never overwrites). `--dry-run` validates and shows the plan with no browser and no Send.
- Run it once. The run id is printed first, so an interrupted command can still be resumed. If it prints "Nothing was sent", a corrected rerun is safe.
  Otherwise it names the exact `headless.py resume --run-id <id>` command: a second run is a second request, so recover instead of rerunning.
- Use it only because the user invoked it. Never run it on your own initiative (for a test, a demo or a check): it spends the user's
  ChatGPT account. A signed-out, busy or blocked profile is reported as it is; no foreground browser, cookies, API key or workaround.
- Reply with the saved path and the generation time it printed, and look at the image before saying it matches the request.
