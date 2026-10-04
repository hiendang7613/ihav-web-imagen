# OpenCLI DOM route

Use only when the host permits terminal browser automation and the user's request
authorizes it. A Computer Use-only host rule takes precedence. OpenCLI must already
be installed and its Browser Bridge connected to the intended Chrome profile.
Do not install an extension or expand permissions merely to pass preflight.

## Quick connection check

```sh
python3 <skill-directory>/scripts/preflight.py
```

This supports the OpenCLI 1.8.x local status protocol: one bounded loopback GET to
`127.0.0.1:19825/status`, with `X-OpenCLI`. It never calls ChatGPT, reads cookies,
starts/restarts a daemon, or opens a page. Exit 0 means an installed matching
CLI/daemon and an advertised selected connection; exit 2 means a missing,
ambiguous, mismatched or unsupported connection. It does not establish live
extension responsiveness, login or image support.

With several connected profiles, choose the correct one explicitly:

```sh
opencli profile list
python3 <skill-directory>/scripts/preflight.py --context-id <selected-context-id>
```

Keep that ID on subsequent commands as `opencli --profile <selected-context-id>`.
The helper ignores saved default-profile preferences so a stale default cannot
silently change the target account. Verify the selected account in the page.

If the daemon is stopped or the extension disconnected, stop the browser flow.
For authorized setup/diagnosis, follow [OpenCLI setup](https://github.com/jackwener/opencli#2-install-the-browser-bridge-extension).
`opencli doctor` performs a live bridge probe and may start/replace the daemon;
it is not the side-effect-free helper. Restarting may disrupt other sessions.
The current project's Chrome Web Store option avoids unpacked-extension setup.

## Own a session and target the exact tab

Read installed `--help` when versions/options differ. These examples were checked
against OpenCLI 1.8.7. Choose a unique task session instead of binding the user's
active tab.

```sh
opencli browser <session> --window background open 'https://chatgpt.com/space/files?tab=images'
# Retain the returned target ID and pass it to each following command.
opencli browser <session> state --tab <target-id>
opencli browser <session> find --role button --name New --tab <target-id>
opencli browser <session> click <fresh-new-ref> --tab <target-id>
opencli browser <session> state --tab <target-id>
```

`open`/`tab new` return target IDs; creating a tab alone does not select a new
default target. Explicit `--tab` avoids accidental routing. Background mode
isolates control from the active Chrome window, but the site may pause rendering
while hidden. A frozen placeholder is not a completed image; report a need for
foreground observation if encountered.

Use observed roles/names or fresh DOM refs. Semantic `--name` is a substring
match; require one intended match for a write. Do not use `--nth 0` just to silence
ambiguity. A route change needs fresh state. Check a `reidentified` target before
another write.

```sh
# Read empty editor first; preserve unexpected drafts.
opencli browser <session> get value <fresh-editor-ref> --tab <target-id>
opencli browser <session> fill <fresh-editor-ref> '<properly quoted prompt>' --tab <target-id>
opencli browser <session> state --tab <target-id>
# After exact draft, attachment, Send-readiness checks and intent recording:
opencli browser <session> click <fresh-send-ref> --tab <target-id>
opencli browser <session> state --tab <target-id>
opencli browser <session> get url --tab <target-id>
```

Require `verified=true` and exact fill text; inspect enabled Send separately.
Keep user text as an argument, using an argument array or proper shell quoting.
For edits use documented `upload` for only the requested files and verify the
whole ready attachment inventory. Any `eval` is read-only DOM inspection. Avoid
network capture, auth/cookie commands, private endpoints, and JavaScript writes.

OpenCLI 1.8.7 journals command IDs when supported by its extension. Reconcile
`command_result_unknown`, `command_lost`, and `result_evicted`; do not manually
replay them. `clicked=true` proves dispatch, not a posted ChatGPT message.
Preserve URL/turn identity and observe the same conversation after a submit error.

Retain the generation tab while opening a second owned tab for library verification.
Preserve final/uncertain chats for handoff. Release only task-owned temporary tabs
after recording useful URLs; never close a user-owned tab/window.

## Avoid blind use of the bundled image adapter

`opencli chatgpt image` exposes `--sd` to skip downloading, but the installed 1.8.7
source clears the draft, reports send success after click dispatch without
posted-message confirmation, and suggests rerunning a timed-out generation. Its
generated-URL detection is not a library check. Use the explicit DOM flow above.
Reassess against installed source before considering a later adapter.

Source: [image adapter](https://github.com/jackwener/opencli/blob/v1.8.7/clis/chatgpt/image.js),
[send/image helpers](https://github.com/jackwener/opencli/blob/v1.8.7/clis/chatgpt/utils.js),
[session/target contract](https://github.com/jackwener/opencli#for-ai-agents).
