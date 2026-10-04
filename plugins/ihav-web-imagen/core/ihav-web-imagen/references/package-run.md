# Package runner (fast headless route)

`scripts/package_run.py` turns one or more request packages into original image files in one process, without the
tool-by-tool control that dominated the first connected-tab run (about 115 s before Send and 160 s of checks after
the image, for one image of about 1.5-3 min generation; n=1, one connected-tab run).

```bash
IMAGE_SKILL="$HOME/.codex/skills/ihav-web-imagen"   # or wherever the skill is installed
IMAGE_PYTHON="$(python3 "$IMAGE_SKILL/scripts/runtime.py" doctor | python3 -c 'import json,sys; print(json.load(sys.stdin)["state_dir"])')/venv/bin/python"

"$IMAGE_PYTHON" "$IMAGE_SKILL/scripts/package_run.py" \
  --package /abs/case_a --package /abs/case_b --download-dir /abs/out \
  --concurrency 2
```

## Package

A folder with `prompt.txt` (1-16000 characters) and `package.json` whose `order` lists at most two reference files
of the folder, in prompt order. Each entry is a file name, optionally followed by a note (`"1_source.png (source)"`).
Only those files are uploaded; anything else in the folder is ignored. Names must be distinct and a PNG/JPEG/WebP.

## What one run does

1. Validates every package, writes a `not_sent` receipt with snapshots of the references, and refuses an existing
   output file, all before a browser starts.
   The receipt retains the required reference count and `reference_state=ready`
   after snapshotting. Invalid local references fail before receipt creation.
2. One headless browser; each package gets its own page, run id and receipt.
   Run IDs include a random suffix to distinguish separate invocations within
   the same second. Receipt creation and execution each use the run lock. A caller
   that cannot acquire it returns `run_busy` without writing the active receipt.
3. Fresh chat in Create image mode, references uploaded in order and verified, exact prompt inserted (compared by
   its words: the editor re-flows line breaks), one Send, durable `intent` before the click.
4. Waits for the owned turn. The image counts as final only when the Stop control is gone, no `Preview` badge is in
   the turn, the image is loaded and its key and natural size read the same twice. A preview is never hashed or
   downloaded.
5. Opens the turn's single image, checks the viewer shows the same raster, clicks Download once, publishes the file
   only if its decoded pixels match that raster (`os.link`, never overwrites). Receipt gets sha256, bytes, size.
   `download_stem` is the requested output stem; `download_file` becomes the actual
   filename after the viewer supplies the extension. Publication identity is
   saved before the file appears, so an interruption can reconcile the exact bytes.
6. `--verify-library` also confirms the creation card in Images; off by default (dev runs).

## Phases in the receipt

`phases` (UTC) and `phase_seconds` (from `start`): `start`, `composer_ready` (library page, New, empty-composer
checks), `effort_ready` (only with `--effort`), `upload_ready`, `prompt_inserted`, `intent`, `turn_posted`, `preview_visible`, `final_complete`, `downloaded`,
`library_verified`. `preview_visible` is the first read with an image in the turn that is not yet final; it may be
absent when no preview is shown. Generation time = `final_complete` - `intent`.

## Batches

`--concurrency` (1-4, default 2) bounds the pages in flight; a page never adopts another run's composer. A visible
ChatGPT alert, a login prompt or a verification challenge in any run stops the remaining runs from sending
(`batch_stopped_before_send`, receipt `not_sent`); a run already sent is preserved and never resent. A run that ends
`unknown` (turn not verified, ambiguous ownership, uncertain click) stops the queued packages the same way; a
confirmed turn that is merely slow, or an error before Send, does not. Exit 0 only if every run was downloaded (and
verified when asked).

## Effort (opt-in, not verified live)

`--effort instant|medium` sets the model/effort chip for each package and restores the previous value afterwards. The chip
is a profile-wide account setting, so the option forces one package at a time (`--concurrency` defaults to 1 with it; an
explicit value other than 1 is refused). The chip changes after the fresh composer is verified and is restored once the
package is completely done (upload, Send, wait, download, optional library check), also after a failure. The receipt keeps
`effort` (`requested`, `previous`, `selected`, `state`, `apply_seconds`, `restore_seconds`); `previous` is written before the
change, so an interrupted process can be undone by hand. A restore that fails stops the queued packages (their chip
"previous" would be wrong) and marks the run `effort_restore_failed`; the finished run keeps its result. Phase
`effort_ready` is stamped after the change, so `upload` in `bench_report.py` excludes it and `effort` reports it.
Not exercised on the live UI yet; using it on the headless profile needs the account owner's agreement.

## Observed facts (2026-09-30, dedicated headless profile)

- Composer chip: `button[aria-label="Select ChatGPT model"]`, text `Medium`, menu "Medium, 2 of 5 ... power" and
  models. It renders 1-4 s after the composer and is a per-browser setting (an everyday Chrome profile showed Extra High).
  Nothing was changed; no speed effect of the setting has been measured.
- The inspected reference tile is a `div[role="button"][aria-label="<name>"]`
  containing the image and a native `button[aria-label="Remove <name>"]`. The name
  may carry a dedupe suffix such as `1_source(4).png`. See the shortened markup in
  [Headless](headless.md). A visible
  progressbar blocks readiness while the local image preview is already loaded.

## Recovery

Use the same run ID to observe an interrupted request:

```bash
"$IMAGE_PYTHON" "$IMAGE_SKILL/scripts/headless.py" resume \
  --run-id retained-run-id --wait-seconds 120
```

Once Send intent exists, this command observes the retained conversation and
cannot submit again. It verifies library persistence before completing a pending
original-file download. A new package command creates new requests and must not
be used to recover an `intent`, `unknown`, or `confirmed` receipt. For legacy
receipt limitations, see [Headless recovery](headless.md).

Preparation and upload errors retain `diagnostic.prepare` or `diagnostic.upload`
in the receipt and JSON summary. These include the failed stage, owned control
labels, readiness, count and busy state. Prompt text and image bytes are excluded.

## Not proven

Measured timings of this runner are recorded below only when a live run completed; generation speed itself is
ChatGPT's, not the runner's. Nothing here promises a speed multiplier or unlimited usage.
