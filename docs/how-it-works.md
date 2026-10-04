# How it works

`/ihav-web-imagen:imagine a red fox` (or `$ihav-web-imagen:ihav-web-imagen ...` in Codex) runs one script, `scripts/imagine.py`. It turns your words into a one-off
package, runs the package runner once and prints where the file went (the current directory, or the folder you pass with `--out`).

```
your words ─► package (prompt.txt + ordered references)
              │  preflight: every reference is structurally checked BEFORE anything exists
              ▼
          receipt  send_state = not_sent        (durable, on disk, under a run lock)
              │
   headless browser, your signed-in profile, the normal ChatGPT Images page
              │  fresh chat · Create image selected · references uploaded in order · exact prompt inserted
              ▼
          receipt  send_state = intent          ← written BEFORE the one click
              │  click Send (once)
              ▼
          observe the owned turn ─► confirmed ─► image is final (no Stop, no Preview badge, same pixels twice)
              │
              ▼
          open that turn's image, check the viewer shows the same pixels, download the ORIGINAL file, publish it (never overwrites)
```

## What is on disk

The plugin owns a dedicated state directory, separate from your everyday browser:

- macOS: `~/Library/Application Support/ihav-web-imagen`
- Linux: `${XDG_DATA_HOME:-~/.local/share}/ihav-web-imagen`
- Override: `IHAV_WEB_IMAGEN_HOME`

Its layout is:

```text
ihav-web-imagen/
  venv/                          private Python environment
  config.json                    runtime configuration
  profile/                       signed-in CloakBrowser profile
  chrome-profile/                optional Google Chrome profile
  profile.lock                   coordinates access to the profile
  headless-images/<run-id>/       receipt.json and reference snapshots
```

The downloaded CloakBrowser Chromium is cached separately in `~/.cloakbrowser`.
It is downloaded during setup, not bundled with this repository. Run state includes
private prompt text, references and authenticated browser sessions; do not publish it.
The runtime does not pause or manage a background research worker.

One folder per run retains `headless-images/<run id>/receipt.json` plus reference snapshots.
The receipt holds the full prompt text and its hash, `send_state`, `send_clicks`, the conversation URL, per-phase timings (`phase_seconds`), the saved file
(`original_file`: path, sha256, bytes, size) and, on failure, `reason`, `error_kind` and `retry_safe`. The image itself goes where you point it.

## Phases (seconds since `start`)

`start` → `composer_ready` → `upload_ready` → `prompt_inserted` → `intent` → `turn_posted` → `preview_visible` → `final_complete` → `downloaded`
(→ `library_verified` with `--verify-library`). Generation time is `final_complete − intent`; everything before `intent` is control overhead,
which the tool reports separately from ChatGPT's own generation time. It does not make ChatGPT generate faster.

## Setup and sign-in

From a clone, run:

```sh
python3 plugins/ihav-web-imagen/core/ihav-web-imagen/scripts/runtime.py setup
python3 plugins/ihav-web-imagen/core/ihav-web-imagen/scripts/runtime.py login
python3 plugins/ihav-web-imagen/core/ihav-web-imagen/scripts/runtime.py doctor
```

For an installed plugin, use the same `scripts/runtime.py` inside its skill directory;
your agent can resolve that path. These commands do not send an image request.

Setup needs Python 3.11+ with `venv`. It creates the private environment, installs
`cloakbrowser==0.3.32`, `playwright==1.62.0` and `pydantic==2.13.5` from
[`runtime-requirements.txt`](../plugins/ihav-web-imagen/core/ihav-web-imagen/scripts/runtime-requirements.txt),
downloads CloakBrowser Chromium with `python -m cloakbrowser install`, and writes
`config.json`. The browser download is about 200 MB; setup requires network access.

Login opens a visible browser window. Sign in yourself, then close it. For your
separate Google Chrome profile, use `runtime.py login --browser chrome`; Google
Chrome must be installed. `doctor` reads local installation state; it does not
establish that ChatGPT is signed in or image generation works.

The runtime targets macOS and Linux x64/arm64 where CloakBrowser supplies binaries.
Login requires a graphical display. Windows is not supported: profile locking uses
`fcntl`. The adapter currently expects English ChatGPT UI. These platform targets
do not constitute verified fresh-machine image generation.

## Local adapter and execution

The rendered-UI adapter is vendored in `scripts/chatgpt_web/`. The image scripts
import that adapter rather than a maintainer's private runtime. `imagine.py`
hands execution to the dedicated environment; if setup is missing, it reports the
setup command instead of sending anything. Only a direct image request authorizes
generation. An uncertain send must be resumed using the same receipt and browser.

## Files

| Path | What it is |
|---|---|
| `plugins/ihav-web-imagen/core/ihav-web-imagen/scripts/imagine.py` | the one-line front door |
| `.../runtime.py` | one-time setup, manual login and read-only doctor |
| `.../runtime-requirements.txt` | pinned runtime dependencies |
| `.../chatgpt_web/` | vendored browser and rendered-page adapter |
| `.../package_run.py` | package → receipt → browser run → original file |
| `.../headless.py` | browser lifecycle, turn binding, receipts, `resume`, `status`, `probe` |
| `.../image_files.py` | reference snapshots, structural preflight, upload checks, original-file publication |
| `.../tests/` | offline suite: failure semantics, decoder differential, real-encoder samples, runner recovery |
| `plugins/ihav-web-imagen/claude/skills/imagine/SKILL.md` | the user-invoked Claude Code wrapper |
