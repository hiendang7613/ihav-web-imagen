# Provenance of `chatgpt_web/`

This package is the ChatGPT web adapter that ihav-web-imagen v0.1 imported from the maintainer's
private `chatgpt-parallel-research` skill (version 0.5.0, not published). v0.2 vendors the parts the
image scripts use, so the plugin no longer needs that skill, its venv, its state folder or its
background worker.

| File | Source (`chatgpt_research/…` in 0.5.0) | sha256 of the source | What changed |
|---|---|---|---|
| `browser.py` | `browser.py` | `2d7b18dd3e27d32ef91d3656b2f47ffa99c8f0a86031f88797e990b7da2265a0` | `start()` takes `accept_downloads` and resolves the Chromium through the new `cloak_binary()` (the configured path, else the one `runtime.py setup` installed; it never downloads during a run). One probe message no longer says "research". Everything else is verbatim. |
| `observation.py` | `observation.py` | `1686f552d7daebacdc3b1cacf50ff2972ec31aa4f3299675d225d55f8eda178a` | Verbatim. |
| `markdown.py` | `markdown.py` | `a733358637ddf9e5556fd635b342e9b20e6de89682fcce6837db4bd54b5d75f0` | Verbatim. |
| `core.py` | `core.py` | `4cd3d9ea6ebebcf72c94551ccb96b6b21d1c8dcdd04243750829c0205b927222` | Only `ResearchError`, `StrictModel`, `now`, `digest`, `json_bytes`, `atomic_write`, `read_object`, `saved_url`. `RuntimeConfig` keeps three fields (`schema_version`, `browser_binary`, now optional, and `headless`). `load_config` returns the defaults when there is no `config.json`. The state folder moved to `paths.py`. Job, batch, campaign and worker models are not ported. |
| `host.py` | `host.py` | `36e0fb45a0128d1d9a0c84d47a41ea1928ca2ce1ce25f7e8a08eabbadd695015` | Only `profile_lock`, which now creates the state folder. The launchd service and setup code are not ported. |
| `paths.py` | new | | The state folder for macOS, Linux and `IHAV_WEB_IMAGEN_HOME`, in the standard library only. |

The upstream `browser.py` observation logic was informed by `miuuyy/codex-chatgpt-web` at `e85e369`
(MIT), according to the upstream provenance record; that record says no source was copied from it.
The upstream `markdown.py` came from an earlier, unpublished tool in the maintainer's workspace; no LICENSE or NOTICE was found next to it. The maintainer, its author, confirmed on 2026-10-04 that it is released here under this repository's MIT licence.
