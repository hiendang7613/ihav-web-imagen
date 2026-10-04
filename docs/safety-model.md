# Safety model: one request per run, never re-sent

The tool spends your ChatGPT account (a generation counts against your plan). The design goal is narrow and testable:
**a run clicks Send at most once, and anything uncertain is recovered read-only, never repeated.** It is *not* a claim of exactly-once
delivery: ChatGPT may fail or refuse for its own reasons.

## The receipt state machine

```
not_sent ──(durable intent written)──► intent ──(click)──► confirmed
    │                                     │
    │                                     └──(click error, timeout, lost page, ambiguous turn, alert)──► unknown
    └──(failure before intent)──► stays not_sent  ⇒ retry_safe: true
```

| `send_state` | Meaning | May a new request be sent? |
|---|---|---|
| `not_sent` | nothing was clicked | yes, after fixing the cause (`retry_safe: true`) |
| `intent` | written just before the click; a command that ends normally (success, failure or Ctrl-C) turns it into `unknown` or `confirmed` | never; if the process is killed outright it can remain, and every reader treats it as `unknown` |
| `confirmed` | the posted turn with your exact prompt was found | never; wait, `resume` or download |
| `unknown` | a click may have happened but the turn was not verified | never; `resume` observes the retained conversation read-only |

## Rules that hold in code (and in tests)

1. `submit_once` refuses unless the state is `not_sent`: no state can type or click twice (`tests/test_failure_semantics.py`; during development each of these rules was broken on purpose to check the suite goes red).
2. A command that ends normally never leaves `intent` behind (a hard kill can; readers then treat it as `unknown`), and `send_clicks` equals the clicks that really happened.
3. `resume` and `status` never click; `generate` on an existing run id only points to `resume`.
4. A batch that meets an `unknown` run stops queued packages at the last point before their own durable intent.
5. A failure outside any single package reports `retry_safe` from **all** receipts: true only if nothing in the batch was submitted.
6. The prompt must appear **exactly** (compared by its words) in a bubble that belongs to this run; two candidates or a foreign turn is `unknown`.
7. An image is final only when the Stop control is gone, no `Preview` badge shows, it is loaded and reads the same twice; the file saved is the
   original from that turn's viewer, accepted only if its pixels match the rendered image.

## Typed errors

Every failure prints `error_kind` (`ambiguous_send`, `site_state`, `busy`, `run_state`, `input`, `download`, `environment`, `ui`, `other`) and
`retry_safe`. `retry_safe` has one meaning: no image request was submitted for this run. A test fails if a new error code has no kind.

## What is NOT claimed

- Not verified end to end against today's ChatGPT UI after the latest hardening: those parts are covered by local fixtures and a differential
  against Chromium and Pillow, not by a live run. See the README's status section for the current live-verified list.
- ChatGPT can change its page at any time; a signed-out, rate-limited or blocked account is reported as it is and never worked around.
