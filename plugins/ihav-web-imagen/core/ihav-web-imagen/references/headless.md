# Headless ChatGPT Images

Use this route when the user requests headless operation or authorizes background
browser control. It supports one image request in a normal ChatGPT chat, with up
to two image references for generation or editing,
and verifies its result in **Images → Your creations**. Preserve a request for
the user's ChatGPT library; a local API/Codex output has a different destination.

## Runtime and ownership

`scripts/headless.py` uses the vendored adapter in `scripts/chatgpt_web/` (its `Browser`, configuration and OS
profile lock) and the private venv that `scripts/runtime.py setup` creates. The state folder is
`~/Library/Application Support/ihav-web-imagen` on macOS, `${XDG_DATA_HOME:-~/.local/share}/ihav-web-imagen`
elsewhere, or `IHAV_WEB_IMAGEN_HOME`. The dedicated profile must already be signed in to the intended account:
the user does that once with `runtime.py login`. The helper starts that browser with `headless=True`. Downloads
are disabled by default and enabled only for an explicit original-file destination. It does not install
packages, copy a Chrome profile, export cookies, inspect credential files, or call private ChatGPT HTTP endpoints.

If the runtime or authentication is missing, report it and name `runtime.py setup` or `runtime.py login`. Do not
sign in for the user or switch to a desktop route for a headless request.
Respect any host restriction on terminal browser automation; this skill grants
no extra access. A background or off-screen Chrome window is a separate route
and does not establish that a process is headless.

One process at a time owns a profile (`profile_busy` otherwise); there is no background worker to pause, and
`--pause-idle-worker` is accepted only for older commands and changes nothing. It never clears browser locks or
kills an unrelated process. The per-run OS lock prevents two callers from submitting under the same run ID.

## Browser choice: CloakBrowser (default) or Chrome

A run uses CloakBrowser and the runtime's own profile unless `--browser chrome` is given on `generate`, `probe`, `login` (or on
`imagine.py` / `package_run.py`). Chrome means plain Google Chrome, found in `/Applications`, `/usr/bin/google-chrome` or at `IHAV_WEB_IMAGEN_CHROME`, launched
through Playwright's defaults in `<state>/chrome-profile`: a profile of its own, never the everyday Chrome profile and never a copy
of one, and with no anti-detection arguments. Consequences:

* Sign in once: `runtime.py login --browser chrome` opens a visible Chrome window on ChatGPT; the user signs in and closes it. It sends
  nothing. A signed-out profile makes a run stop before the Send, like the CloakBrowser one.
* A Chrome run does not take the CloakBrowser profile lock. Two Chrome runs cannot share the Chrome profile (`profile_busy`).
* The receipt records `browser`. `resume` and `status` always use the browser the run was created with (a receipt from before this
  option means CloakBrowser); they refuse `--browser`.
* A missing Chrome is `browser_binary_missing` (kind `environment`), reported before any Send. `--dry-run` checks that Chrome exists,
  not that it is signed in.
* Plain Chrome is more likely than CloakBrowser to meet a verification challenge; the run then stops (`human_verification_required`)
  and is never retried or worked around. Nothing in the Chrome path has been run live yet: offline tests with a fake Playwright only.

## Commands

Use the venv `runtime.py setup` made (`imagine.py` hands itself over to it automatically):

```bash
IMAGE_SKILL="$HOME/.codex/skills/ihav-web-imagen"   # or wherever the skill is installed
IMAGE_PYTHON="$(python3 "$IMAGE_SKILL/scripts/runtime.py" doctor | python3 -c 'import json,sys; print(json.load(sys.stdin)["state_dir"])')/venv/bin/python"

"$IMAGE_PYTHON" "$IMAGE_SKILL/scripts/headless.py" probe
```

`probe` opens the authenticated Images page, selects Your creations, chooses
New, and verifies an empty image composer. It leaves the editor blank and sends
nothing. It tests the actual browser flow, rather than just a connection status.
Exit 0 establishes that this preflight passed at that time.

To test two references without sending a generation prompt:

```bash
"$IMAGE_PYTHON" "$IMAGE_SKILL/scripts/headless.py" probe \
  --reference /absolute/path/1_source.png \
  --reference /absolute/path/2_guide.png
```

This probe uploads the files to ChatGPT, waits for ready attachment controls in
the requested order, and removes its own composer attachments on success. Those
uploads can remain in the account's Files library. `probe_attachments_removed`
describes composer cleanup; it does not claim account-file deletion. The editor
stays blank and Send is never clicked. Local path, format, size and duplicate-name
errors fail before browser/profile acquisition. Unsupported or unfinished tiles
stop with a compact `diagnostic.upload` inventory; unrelated drafts are preserved.

For an authorized new image, put the exact prompt in a UTF-8 text file and select
a new, safe run ID. Do not reuse the vase test's ID for another generation.

```bash
"$IMAGE_PYTHON" "$IMAGE_SKILL/scripts/headless.py" generate \
  --run-id new-image-001 --prompt-file /absolute/path/prompt.txt \
  --preview
```

The receipt is stored under `<state>/headless-images/<run-id>/receipt.json`, with
mode 0600. It retains the prompt and its hash, pre-send library baseline hashes,
submission intent, conversation/turn identity, output dimensions, and separate
generation and library states. CLI summaries omit prompt text and asset URLs.
`--preview` captures the rendered UI for visual inspection. An original-file
download uses the viewer's Download file control and preserves the returned bytes.

For a generation/edit with two references and a requested original file:

```bash
"$IMAGE_PYTHON" "$IMAGE_SKILL/scripts/headless.py" generate \
  --run-id reference-edit-001 --prompt-file /absolute/path/prompt.txt \
  --reference /absolute/path/1_source.png \
  --reference /absolute/path/2_guide.png \
  --download-file /absolute/path/result.png \
  --requested-size 2912 368 --preview
```

References are PNG/JPEG/WebP, at most 20 MiB each, with distinct filenames. Their
immutable snapshots, SHA-256, byte count, order and decoded dimensions are kept
under the run directory. Upload uses the checked byte buffers. `resume` reuses
the retained inputs; it cannot replace them. Incomplete reference preparation
blocks a text-only replacement submission. Before any receipt exists each reference
also gets a structural preflight. PNG: chunk framing (IHDR first and unique with valid bit depth /
colour type / compression / filter / interlace values, PLTE before IDAT and not larger than the bit
depth can index, consecutive IDAT, IEND, known critical chunks) and the CRC of **every** chunk. JPEG:
segment framing through a non-empty scan, SOF with a precision that fits its process, a non-zero height and
1, 3 or 4 components (T.81 allows more, but both decoders reject every other count on genuine files), no
hierarchical/differential frames, scan selectors that exist in the frame (and, when the frame's ids are unique,
are not repeated and follow frame order). WebP: container size. A parser failure is `reference_image_invalid`, never a raw
exception.

The evidence bar for a rule: a mainstream decoder rejects the input, or picture data is missing, or it is
cheap framing sanity that no real file violates (stated as such). `tests/decoder_differential.json` records
Chromium's and Pillow's verdicts on 69 rows beside the policy verdict, and re-measures Chromium on every
run. It shows why the rules are what they are: a bad CRC even in a tEXt chunk is rejected (Chromium skips the
chunk, Pillow rejects the file); invalid IHDR values, a PLTE larger than the depth, a repeated or out-of-order
scan selector and a zero SOF height are rejected by the decoders; duplicate or all-zero JPEG component ids,
DNL segments, a PLTE in a grey image and the like are decoded by both, so they pass. `tests/test_real_encoder_samples.py`
keeps 32 real encoder outputs (Pillow, libjpeg-turbo: baseline, progressive, arithmetic, restart intervals,
CMYK, EXIF/ICC, palette, 16-bit, animated PNG/WebP) that must always pass and decode; on 7.5k real images of
this machine no verdict changed. The preflight does not inflate PNG IDAT data or decode JPEG entropy,
and the Chromium decode used before upload accepts a PNG whose IDAT data is damaged, so neither check
replaces the other; both run before upload and Send. A pass means the checked structure passed, not that
every compressed pixel stream is conformant.

`--requested-size` records desired dimensions for later comparison. The helper
does not select an exact pixel size or force an extreme ratio. Include the
requested composition and ratio in the prompt, inspect the native result, and
record both sizes. A prompt hash identifies the request, not a deterministic
image seed or guaranteed repeat of the raster.

The explicit output path is preserved exactly. Its extension must be `.png`,
`.jpg`/`.jpeg` or `.webp` and name the native download format; a different, unknown
or missing extension (for example `.png` for a WebP, or `.txt`) writes nothing and
the run reports `download_extension_mismatch` with both suffixes; retry
`resume --download-file` with the native extension. `original_file` records its SHA-256,
byte count, MIME, UI filename and actual pixel dimensions. Existing files are
preserved. Retained publication evidence can reconcile an interrupted save using
the exact file hash. The package runner uses `download_stem` until the viewer
supplies its file extension, then records the final path in `download_file`.
Changing the destination on `resume` starts verification for that new file;
previous completion is retained separately in `last_original_file`. A failed
fresh resume clears current library/download verification while preserving the
original identity for reconciliation.

After interruption or an uncertain Send result, use the same ID:

```bash
"$IMAGE_PYTHON" "$IMAGE_SKILL/scripts/headless.py" resume \
  --run-id new-image-001 --preview --wait-seconds 120

"$IMAGE_PYTHON" "$IMAGE_SKILL/scripts/headless.py" status --run-id new-image-001
```

`resume` opens the retained normal conversation and observes it. Once submission
intent exists, it never fills the editor or clicks Send. A receipt that proves
`not_sent` can complete its first submission after corrected preflight checks.
Each resume clears the current library-verification flag and retains earlier
proof separately; a failed fresh observation cannot reuse past success.
If the retained URL is missing, stop and preserve the uncertain receipt; do not
create a replacement run for the same request. `status` reads the local receipt
without launching a browser. Repeating `generate` for an existing ID is rejected
and preserves the earlier receipt.
An existing unreadable or invalid receipt also blocks a new submission. Preserve
it for investigation rather than deleting it to make another generation possible.

Package runner receipts share this recovery contract. For older runner receipts
that put a filename stem in `download_file`, explicitly supply the desired file
path when observing an already-sent run. Older unsent receipts with contradictory
reference counts need inspection; do not silently discard required references
or delete the receipt. Confirm `not_sent` before preparing a separate corrected
request. Never create a replacement for `intent`, `unknown`, or `confirmed`.

Each observation has a finite budget: default 600 seconds, maximum 1200. An
explicit `resume` begins another bounded observation of the same request. A
deadline ends observation; it does not cancel generation or authorize another
submission. A visible ChatGPT alert ends the observation promptly and preserves
submission evidence. Exit 2 can mean blocked, pending, unknown, or an unverified
library result: read the JSON states before interpreting it as a failure.

`generate --effort instant|medium` temporarily selects that model/effort chip value in the new chat and restores the previous
one after the run (see [package runner](package-run.md) for the receipt fields). It is opt-in and not exercised on the live
UI yet: it changes a profile-wide account setting, so it needs the account owner's agreement before any live use.

Every failure names its class and whether a retry is safe. `error_kind` is one of `ambiguous_send`, `site_state` (login,
verification, alert), `busy` (profile, worker, another generation), `run_state` (the run exists or cannot be recovered: use
`resume`/`status`), `input` (prompt, reference, package or destination), `download`, `environment`, `ui` (the page was not
as expected) or `other`. `retry_safe` has one meaning: **no image request was submitted for this run**, so trying again
cannot create a duplicate. It comes from the receipt (`send_state=not_sent` and no Send click) and is always false for
`ambiguous_send` and `run_state`. A confirmed turn whose download failed is not retry-safe: `resume --download-file`. Both
fields appear in the CLI output and the receipt summary; `tests/test_error_kinds.py` fails when a new error code has no kind.
For a batch (`package_run.py`) the verdict comes from every package's receipt: a failure around the batch (session start or
teardown) prints `retry_safe: true` only if no package was submitted, and lists `runs` with each package's own verdict; a
receipt left at `intent` is `unknown`. A package owned by another process reports `run_busy` (`run_state`, never retry-safe)
without touching that owner's receipt.

## DOM readiness and image identity

The live English UI observed on 2026-09-30 has:

- An editor matching `[contenteditable="true"][role="textbox"][aria-label="Ask ChatGPT"]`;
  the helper also supports the older `#prompt-textarea` fixture.
- A selected Chat button with `aria-pressed="true"`, plus a Create image chip.
  The chip's `Remove Create image` control is not an uploaded attachment.
- A Send control inside the editor's form/composer boundary, with an enabled
  application state and a successful hit test before submission intent. The
  editor's words and a fresh chat are checked before Send; UI whitespace reflow is
  normalized while the exact retained prompt has its own SHA-256.
- Image uploads in `[data-composer-attachments]`: a `div[role="button"]` labelled
  with the effective filename, containing a matching loaded thumbnail and a
  native button labelled `Remove <filename>`. A visible `role="progressbar"`
  labelled `Uploading <filename>` remains while the local thumbnail is loaded;
  it disappears when processing and collision renaming complete. The helper
  checks readiness and the complete ordered inventory again before Send.
- Posted messages marked by `data-user-message-bubble`, inside a `data-turn-key`
  boundary. Generated images appear in `generated-image-gallery` and
  `generated-image-preview`, with loaded natural dimensions. Older message author
  roles are covered by local fixtures, not by this live test.
- A long prompt is collapsed in the posted bubble. Read-only measurement of a
  4334-character turn (2026-09-30): the bubble's text is the whole prompt plus a
  leaf `span` reading `…` and a `button` labelled `Show more` (`Show less` once
  expanded; the `…` is then gone). The reference thumbnails are outside the bubble,
  as `div[role="button"][aria-label="User attachment"]` tiles in the turn's user
  unit.

Observed image-tile shape (attributes shortened; no image data or asset URL):

```html
<div data-composer-attachments>
  <div role="button" aria-label="1_source.png">
    <img alt="1_source.png">
    <button aria-label="Remove 1_source.png"></button>
    <span role="progressbar" aria-label="Uploading 1_source.png"></span>
  </div>
</div>
```

The progressbar above represents the uploading state. After processing, the
filename may become `1_source(5).png` across the tile, image alt and removal
control. A loaded local preview or enabled Send alone cannot prove readiness.

The observer requires one posted prompt whose words equal the retained prompt in
the owned fresh chat. A trailing `…` and `Show more`/`Show less` are dropped only
when the bubble contains such a toggle button; any other extra text, a prefix, or
a different prompt does not match. Each read records `observation_last` (counts of
matched bubbles, images, generating and preview state; no text), so an unverified
run states what the last read saw. It scopes
image selection to that turn, excludes hidden message duplicates and earlier
turns, and waits for loaded images without a visible Stop control. A matching
conversation URL, placeholder, avatar, image toast, or vanished spinner alone
does not establish completion.

Cross-page blob URLs are not stable file identity. Library verification uses
either the same file ID already exposed in rendered image URLs, or a SHA-256
fingerprint of a 32×32 rendering of the loaded image. For the latter, it opens a
new library card, waits for the viewer to render the expected natural dimensions,
and compares the rendered pixels. Only hashes leave the browser; no image bytes
are fetched or exported by that comparison. A filename or dimensions alone never
pass the check. Cross-origin canvas restrictions or too many new candidates leave
`library_verified=false`, preserving the completed conversation.

After the helper returns, inspect its task-owned UI preview with `view_image` and
check the requested subject, count, aspect ratio, background, and style. The
helper verifies creation and library identity; it does not replace that visual
acceptance check. Do not automatically regenerate a result that misses the brief.

## Measured evidence and limits

The 2026-09-30 vase test used the existing authenticated headless profile and
exactly one Send click. It produced one 1254×1254 image and verified the opened
library card by rendered-pixel match. The original asset was not downloaded.
Composer setup took 13.941 seconds. The first observer used obsolete message
selectors and needed read-only reconciliation after a compatibility fix, so
generation latency is unknown; the receipt records it as null. This is one
assisted live case, not a bulk reliability or speed benchmark.

Two-reference upload controls and their processing/rename states were inspected
live on 2026-09-30 without Send. A full two-reference probe returned a control
timeout; do not call that probe successful. The first package run with two
references (a 6047-character prompt) uploaded both files in order and reached
Send, then ended `unknown/posted_prompt_unverified` because the exact-equality
matcher could not see through the collapsed bubble's `…`/`Show more` tail. The
matcher now accepts that tail (fixtures built from the measured markup). It has
not yet been confirmed on a live Send with a long prompt; local fixture passes do
not establish that browser outcome. That run was never resent and stays `unknown`.
Live original-file download of an owned turn has been observed since: two
one-line runs on 2026-10-01 (short prompts, no references) completed with
`downloaded: true`, the saved PNG's pixels matching the rendered image and the
library card matched.

The separate [package runner](package-run.md) implements bounded multi-package
execution. Concurrency throughput and image-edit quality have not been measured
by this evidence. Locale selection is not implemented. The route uses the
installed runtime rather than shipping
an independent browser environment. Site changes, sign-out, account blocks, and
missing runtime dependencies remain observable failures. Do not infer backend
model names, exact pixel control, subscription quota, or unlimited usage from
an adapter name or a single successful run.

Run the local regression suite without provider calls:

```bash
"$IMAGE_PYTHON" -m unittest discover \
  -s "$IMAGE_SKILL/tests" -p 'test_*.py' -v
```

The rendered fixtures use isolated temporary profiles and local `file://` pages.
Mocks cover uncertain Send results, durable intent, duplicate commands, profile
locking, and cleanup. These checks supplement the recorded live
case; they do not establish future site compatibility.

Primary browser contract: [Playwright persistent contexts](
https://playwright.dev/python/docs/api/class-browsertype#browser-type-launch-persistent-context).
Relevant adapter evidence is in [repository findings](repository-findings.md).
