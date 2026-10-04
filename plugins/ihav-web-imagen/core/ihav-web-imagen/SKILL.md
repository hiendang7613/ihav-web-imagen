---
name: ihav-web-imagen
description: Draw one image from a description with the user's own signed-in ChatGPT account through the rendered ChatGPT web UI (no API): headless, one request per run, original file saved. Quick use - Codex `$ihav-web-imagen:ihav-web-imagen a red fox` (plugin) or `$ihav-web-imagen a red fox` (linked skill); Claude Code `/ihav-web-imagen:imagine a red fox` (plugin) or `/ihav-web-imagen a red fox` (linked). Also documents manual UI routes and recovery.
---

# ihav-web-imagen: ChatGPT images from Claude Code and Codex

Create the requested image in the user's signed-in ChatGPT account, starting at
<https://chatgpt.com/space/files?tab=images>. A separate ImageGen/API result does
not establish that destination. The one-line command below saves the original file (current directory or `--out`); the manual routes download only when requested. The user handles
sign-in; never collect credentials/cookies or call private ChatGPT endpoints.

## Quick start: `$ihav-web-imagen <what to draw>`

In Codex it is `$ihav-web-imagen <what to draw>` (`$ihav-web-imagen:ihav-web-imagen` when installed as a plugin); in Claude Code `/ihav-web-imagen <what to draw>` (`/ihav-web-imagen:imagine` as a plugin). See the repository README to install. When the user gives just a description (for example
`$ihav-web-imagen a beautiful girl`), run one command and report the file:

```bash
python3 <skill-directory>/scripts/imagine.py - <<'TEXT'
<the user's words, unchanged>
TEXT
```

Write the description through a **quoted heredoc** like this, so `$`, backticks, backslashes and quotes reach the prompt exactly as typed
(a double-quoted argument would let the shell alter them). Options go after the `-`, for example `imagine.py - --ref photo.png <<'TEXT'`.

**First use on a machine** (both send nothing): `python3 <skill-directory>/scripts/runtime.py setup` makes a private venv with the
pinned packages and downloads the CloakBrowser Chromium (about 200 MB, once); then the user runs `python3 <skill-directory>/scripts/runtime.py login`
and signs in to ChatGPT in the window it opens, then closes it. Only the user can sign in: offer the command, never type credentials.
`runtime.py doctor` reports what is missing. If `imagine.py` says the runtime is not set up, run `setup` (it is safe) and then ask the user to log in.

It hands itself over to the runtime venv, builds a one-off package, sends **one** request through the headless profile and saves
the original file in the current directory, or in the folder you pass with `--out DIR` (never overwrites); expect 1-3 minutes. Options: `--ref IMAGE` (up to two, in
prompt order, for edits), `--out DIR`, `--name STEM`, `--browser chrome` (plain Google Chrome in a profile of its own, instead of the default CloakBrowser: only when the user asks for Chrome), `--verify-library`, `--wait-seconds N`, `--json`, and `--dry-run` (validates and
shows the plan and checks the output folder: no browser, no Send; it needs `runtime.py setup` too).

- Only a direct request to create or edit an image sends anything. Brainstorming, prompt writing, research and reviewing an existing image do not
  authorize a send; answer those without running the command.
- Use the prompt exactly as given: do not embellish it, and do not ask questions about an ordinary description.
- Run it once (default wait 8 minutes; the run id is printed first, so a killed or interrupted command can still be resumed). On failure read what it
  prints: "Nothing was sent" means a corrected rerun is safe; otherwise it names the exact `headless.py resume --run-id <id>` command, because a
  second run is a second request.
- A signed-out, busy or blocked profile is reported as it is; never work around it (no foreground browser, cookies, API key).
- `--browser chrome` needs a one-time sign-in that only the user can do: `python3 <skill-directory>/scripts/runtime.py login --browser chrome` opens a Chrome window for it, which the user closes when done. A signed-out Chrome profile stops before any Send. `resume` and `status` use the browser the run was created with: never pass `--browser` to them.
- Reply with the saved path and the generation time it printed; look at the image before saying it matches the request.

## Choose a working route before opening a page

Respect the host's browser-control rules and the user's selected browser/tab.
A skill does not override tool access restrictions.

1. **Headless, when requested or background operation is authorized:** read
   [Headless](references/headless.md). Use `scripts/headless.py` with the runtime
   `runtime.py setup` installed and its dedicated, signed-in profile. The helper operates
   the normal rendered UI without opening a desktop window. It supports a prompt
   with up to two PNG/JPEG/WebP references in prompt order, including edit requests.
   Reference hashes, snapshots, upload readiness and order are retained. Use
   `--download-file` only when the user asks for the original file.
   `probe` performs a no-send browser preflight; `generate` creates a new run;
   `resume` reconciles an existing receipt; `status` reads it without a browser.
   If the headless profile is unavailable or signed out, report that condition.
   A request for headless operation does not authorize foreground fallback,
   profile copying, cookie export, or installation of another adapter.
2. **Connected browser tab:** follow the tool's first-call entry-point rules and
   read its documentation. Use an inventory only when a surface must be found.
   Use a specified tab or create one
   task-owned tab in the authenticated profile. Retain its browser/tab identity
   through navigation. Read [Computer Use](references/computer-use.md).
3. **OpenCLI DOM controls:** use when terminal browser automation is permitted
   and this route is requested or authorized. Run
   `python3 <skill-directory>/scripts/preflight.py`. This reads local connection
   status without opening a tab, starting a daemon, reading cookies, or sending
   a prompt. Exit 0 means the bridge advertises a connection; it does not prove
   ChatGPT login or image capability. Read [OpenCLI](references/opencli.md).
4. **Native Chrome app, fallback:** use only if the user permits foreground
   operation and can leave the target
   window available. It shares foreground state and is slower. Empty browser
   inventory does not mean an app handle provides isolated tab control. On a
   user-changed-app error, stop writes and reacquire once; if the user is still
   active, report the conflict without opening more tabs.

If no suitable route works, report the specific missing connection and the setup
in the relevant reference. Avoid repeated discovery or the same failed action.
Connection setup requiring new permissions stays subject to the host's approval
rules. Never bypass a blocked app with another automation mechanism.

## Create or edit

1. Open Images and confirm the intended signed-in account/workspace. Record a
   small baseline of the relevant creation cards for later comparison. Choose
   the visible **New** or equivalent creation control. An observed valid flow is
   **Images → New → ChatGPT home**, with **Create image** selected and an **Ask
   ChatGPT** composer. A home URL alone is not a failure or proof of readiness.
2. Confirm image creation is selected, the editor is ready, and there is no
   generating turn, unexpected draft, or attachment. Preserve unrelated drafts;
   use a fresh task-owned chat if available. Never clear a user's draft to make
   a check pass. Avoid temporary chats for a library-persistence request.
3. Preserve subject, style, framing/aspect ratio, and constraints in a short
   prompt. Use the user's requested image count; default to one. For edits,
   verify the selected reference identity and every upload's ready state.
4. Fill/paste once with the selected route's supported API. Verify exact editor
   text, the complete attachment inventory, and an enabled Send control. For
   contenteditable editors, visible text alone does not prove React accepted
   it. Refresh element identities after navigation or rerendering.
5. Immediately before clicking Send, retain a run receipt: route, owned tab ID,
   prompt hash, submission intent time, current URL, and `send_state=intent`.
   Keep it in working context; persist it in the task's local state when a run
   must survive interruption. Confirm Send is unobscured before persisting intent.
   Click Send **once**. Confirm the matching posted
   user message and capture the conversation URL/turn if exposed, then mark
   `send_state=confirmed`. Dispatch success or a `/c/` URL alone is insufficient.
6. Observe that same turn until its finished image is visible. Use compact state
   reads for progress and a screenshot when needed to inspect the actual image.
   A placeholder, uploaded reference, unchanged canvas, or stopped spinner is
   insufficient. Current markup uses `data-user-message-bubble`, a
   `data-turn-key` boundary, and `generated-image-gallery`; older markup uses
   message author roles. Bind output to the exact posted prompt and its turn.
   Ignore hidden copies, earlier turns, and images in unrelated notifications.
   Keep image-generation time separate from control overhead and recovery time.
7. Check the requested visual properties and count. Then open Images in a second
   task-owned tab and verify the matching new creation card, opening it if its
   identity is ambiguous. Preserve the generation tab/URL for recovery. Report
   `library_verified` only when that matching library result is observed. If
   generation succeeds but library persistence is unproven, state both facts.
   Blob URLs are page-local and cannot prove identity across tabs. The headless
   helper compares exposed file IDs or hashes of pixels already rendered in the
   owned turn and the opened library card. A title or matching dimensions alone
   do not establish identity. Inspect the image visually for the requested
   subject, count, background, and framing before reporting success.

## Recovery and speed

- **Before submission intent:** one fresh observation and one corrected action
  are reasonable for a stale element or focus error. If the mechanism still
  fails, diagnose or switch an allowed route instead of repeating it.
- **After submission intent:** a timeout, lost connection, or interrupted action
  means the outcome may be unknown. Reconcile the retained tab/conversation and
  exact posted prompt. Do not resend, click Retry/Regenerate, or start the same
  request in another route. If identity cannot be established, preserve the
  evidence and report `send_state=unknown`.
  A tool-proven pre-dispatch failure permits a corrected first submission after
  fresh draft/turn checks; elapsed time or an empty screen does not prove that.
- Keep one owner per composer. Batch only deterministic actions using the
  current observed state, then return the resulting state in the same call.
  Do not batch a submit before draft readiness is verified.
- Prefer scoped text/DOM state and accessibility diffs. Avoid repeated full
  desktop/tab inventories, screenshots for every click, and reloads of a
  generating chat. Take a screenshot for visual inspection or missing context.
- Use condition-aware observation and the host's built-in waits. Avoid tight
  polling. A finite observation budget (for example, ten minutes for one image)
  ends with a retained URL and pending/unknown status, never a fresh submission.
- Stop at a visible account/login, rate-limit, content-policy, or access-control
  block. Report the observed message and submission state. Do not change plans,
  credentials, providers, permissions, or limits to manufacture success.

## Fast repeated runs

For repeated dev generations from a prepared package (references + prompt), use `scripts/package_run.py`
([Package runner](references/package-run.md)): one process, per-phase timing in the receipt, preview-aware wait, the
original file saved from the owned turn, optional library check, bounded parallel runs. It does not shorten ChatGPT's
own generation time.
Choose this runner when original-file downloads are requested. For a result that
must remain in the ChatGPT Images library, enable `--verify-library`. For a
library-only request, use `headless.py generate` without a download destination.
Resume an existing runner receipt with `headless.py resume --run-id <same-id>`;
do not rerun the package command to recover an uncertain submission.

## Checks and reporting

For a requested **preflight/no-send test**, check the route, authentication,
Images entry and empty image composer; leave the prompt blank and do not submit.
A local diagnostic alone is a connection check, not a browser-flow test.

For an authorized **live test**, generate only the requested image once and
verify its creation card in Images. Report route, submission state, generation
state, library verification, and any retained link. Include measured latency
only for the steps actually timed; never promise a speed multiplier or unlimited
usage. Tests for the local diagnostic are in `tests/test_preflight.py`.

The regression suite in `tests/` covers headless lifecycle, reference uploads,
original-file publication, runner recovery and local preflight. It uses mocked
failure paths and local rendered fixtures. These checks do not establish live
image-edit quality or exact dimensions. Read [repository findings](
references/repository-findings.md) when choosing or diagnosing another adapter.
That reference distinguishes inspected source from actual browser evidence;
third-party claims about quota, backend models, or headless support do not
establish this account's capabilities.

Official guidance: [browser extension](https://learn.chatgpt.com/docs/chrome-extension),
[built-in browser](https://learn.chatgpt.com/docs/browser), and
[image generation](https://learn.chatgpt.com/docs/image-generation).
