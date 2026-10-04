# Repository evidence for maintaining ChatGPT Images

Reviewed on 2026-09-30 for the concrete requirement: generate through the normal
ChatGPT UI, keep the image in the authenticated account’s Images library, and
operate headlessly without downloading the original. This is supporting evidence
for the installed skill, not an instruction to install or execute another package.

## Decision applied to the skill

Use the existing authenticated dedicated headless browser and normal rendered UI.
The implementation reuses the installed browser lifecycle and OS profile lock,
records submission intent before one Send click, reconciles uncertain outcomes,
binds output to the exact posted prompt, and verifies the library by exposed file
identity or rendered-pixel match. It preserves the account’s conversation and
reports control, observation, generation, and library outcomes separately.

Headless operation changes browser control, not the service’s generation speed or
plan limits. An off-screen headed window is a different mechanism. A “no API key”
label can describe Codex authentication, captured web credentials, another service,
or a local model; it does not establish the requested account, quota, or destination.
[ChatGPT and API billing are separate](https://help.openai.com/en/articles/9039756).

## Evidence scope

The combined supplied lists contain 20 distinct repositories, one gist, and two
topic pages. Repository versions are pinned below. Relevant source boundaries were
inspected for routing, authentication, submission, observation, and output; this
was not an exhaustive audit of every file. Fetched file hashes and immutable links
are retained in [repository-sources.json](repository-sources.json). The snapshots
were not installed, and donor provider/browser workflows were not run. The only
donor execution was the fully inspected send function in an offline mock.

The installed helper has local mocked tests and rendered fixtures, plus one
assisted live headless vase case. That case used one Send, produced one square
image, and verified its library card without original-asset export. Obsolete DOM
markers initially required read-only recovery; the result is not an unattended
reliability benchmark. Details and commands are in [headless.md](headless.md).
Supplemental ChatGPT research questions stopped at mode verification before Send;
no generated research reports are being presented as evidence.

## Inspected repositories

### Vieeeeeee/gpt-web-image

Connected Chrome/Computer Use workflow with Chinese UI locators and local asset collection. Its preparation lock is useful, but `sendWithEnter` permits a second Enter after two seconds without a posted-message acknowledgement. An inspected-function offline mock produced two Enter presses and an unknown outcome. An unchanged draft does not prove the first submission was absent. Keep the one-send boundary and durable receipts; do not adopt the replay or unconditional export. [Inspected source at 99ebf632bcf4](https://github.com/Vieeeeeee/gpt-web-image/blob/99ebf632bcf4fc1fe9c3a67bc0fafae469ca36cd/browser-flow.mjs).

### leeguooooo/image-use

The most useful source for the current DOM: user-message bubbles, turn boundaries, generated-image galleries, scoped Send controls, loaded dimensions, and blob output. These markers were independently confirmed in the vase conversation. Its web route depends on chrome-use and its Codex route reads OAuth credentials and calls a private Responses endpoint. Web generation normally exports files and can remove the conversation. Reuse the DOM and pre-/post-dispatch distinction, while preserving the requested library destination and conversation. [Inspected source at 3ac373644f60](https://github.com/leeguooooo/image-use/blob/3ac373644f6086a2379ca5e6761cfe9ee972087a/image-use).

### amzbase-com/chatgpt-automation-extension

The public recursive tree contains documentation and translations, rather than the extension implementation. Concurrency, queueing, usage tracking, automatic download, and recovery are advertised behavior; their actual submission and ownership contracts could not be assessed from this source. Do not treat those claims as evidence of headless support or reliable batch throughput. No extension was installed. [Inspected source at 62d232e823b5](https://github.com/amzbase-com/chatgpt-automation-extension/blob/62d232e823b54ca967cbbd8d0dfdc2536c9ec3fe/README.md).

### Hawkynt/ImageGen

A browser backend with heuristic element discovery and extensive debug captures. Its ChatGPT path attempts temporary chat, exports full-resolution files, and uses fixed settle waits. The shared backend snapshots images after submission; global new-image detection can include unrelated page images. Temporary-chat and export behavior conflict with this task’s persistence goal. Prefer exact composer/turn scope, a pre-send baseline, readiness conditions, and diagnostic captures only when useful. [Inspected source at 93825fcd1b30](https://github.com/Hawkynt/ImageGen/blob/93825fcd1b30f5789b2be759b47527be370e2560/src/backends/chatgpt-backend.ts).

### lownamlee/gpt-image-2-mcp

The direct web backend launches with headless disabled and hides or moves its window. That is useful background control but does not satisfy a strict headless request. It presses Enter, then clicks a nearby Send control after 1.2 seconds if composer text remains; this is another uncertain-submission replay path. It exports local images. The auto backend tries the API first. Preserve explicit route choice, actual readiness, and reported session reuse; do not adopt hidden-window labeling, replay, or a destination-changing fallback. [Inspected source at 9240da04df2a](https://github.com/lownamlee/gpt-image-2-mcp/blob/9240da04df2ab7df3e9fb3926125648aa5ed20f7/src/backends/chatgptWeb.ts).

### leeguooooo/chatgpt-use

Strong request-receipt ideas: safe request IDs, atomic persistence, a status operation, and no replay for accepted/submitted or unknown requests. A dead owner does not prove the request was unsent. The channel uses chrome-use and also includes private project/conversation reads; those operations were not imported. Its current Rust profile-discovery function is a stub, so the comment mentioning a Python cookie reader is not evidence that this Rust function reads cookies. Transfer the receipt and ownership rules, not the entire channel. [Inspected source at 2e4bd0aa3ebf](https://github.com/leeguooooo/chatgpt-use/blob/2e4bd0aa3ebf3dc46a927c7a6c5914a31b907134/src/receipt.rs).

### jessedi0n/openai-chatgpt-chrome-extension

A browser extension around api.openai.com using a configured API key. Its images are API responses stored in extension chat state, rather than demonstrated consumer ChatGPT Images creations. Request cancellation and explicit image/text handling are useful interface ideas. Installing this extension would neither remove API billing nor establish the requested library destination. [Inspected source at 4f76838a0703](https://github.com/jessedi0n/openai-chatgpt-chrome-extension/blob/4f76838a07034cb1329304d6893a4773da4fb3ee/background.js).

### i6ww/gpt2api

A broad account/provider gateway. The inspected current GPT image route dispatches to the private Codex Responses endpoint; older web functions implement private conversation, file-library, upload/download, and Sentinel protocols. HTTP speed is not evidence of compatibility with the requested browser/library contract. Do not import credential pools, challenge protocols, provider fallbacks, or private endpoint calls. Explicit result ownership and retained request identity remain useful design criteria. [Inspected source at 928094d53a4e](https://github.com/i6ww/gpt2api/blob/928094d53a4ed5031b56c23779287b48b9e7be3e/backend/internal/provider/gpt/gpt.go).

### almeidasrenato/codex-image-skills

Two distinct routes: Codex CLI local generation and a chrome-use ChatGPT web workflow. The web code recognizes blob images but downloads the result, archives the conversation through private HTTP calls, and reconciles a local app database. Those cleanup effects exceed a request to leave an image in the library. Its use of stable loaded-image observations is useful; preserve the exact turn and account state instead of importing download/archive/database behavior. [Inspected source at 0acbf30ee567](https://github.com/almeidasrenato/codex-image-skills/blob/0acbf30ee5677515576229618530aa6a4f1c5d60/scripts/gerar.py).

### gustavoleitao/image-describer

Image description and accessibility text through OpenAI vision chat completions, rather than an image-generation transport. It is relevant to defining visual acceptance criteria, not to headless browser control. The existing assistant can inspect the task’s UI screenshot; no extra paid captioning call is needed for this check. [Inspected source at d31091f21c12](https://github.com/gustavoleitao/image-describer/blob/d31091f21c12d1274151f4e11a56fa354ea3512c/gptService.js).

### StartripAI/gpt-image-2.0-workbench

The web mode is a manual copy/paste guide; executable runtimes use the public Images, Responses, and Batch APIs. Templates make subject, composition, text, and preservation constraints explicit. The ledger separates latency, outcome, and error metadata. These ideas inform prompt fidelity and reporting. The code is not a consumer headless library adapter, and its preflight can call a moderation API. Do not infer backend parity, cost, or library persistence from the manual guide’s claims. [Inspected source at 7aea816d5bb3](https://github.com/StartripAI/gpt-image-2.0-workbench/blob/7aea816d5bb3a52d72c023d884837ca986e1c84a/docs/chatgpt-web-mode.md).

### oakplank/gpt-image-bridge

A shell wrapper around Codex CLI that generates in a private temporary directory and copies a PNG to a requested local path. Its skill explicitly distinguishes the coding model from the image backend, and the executable name does not pin a model. That distinction is useful for truthful reporting. The wrapper’s local output and quota assertions do not demonstrate creation in the requested ChatGPT Images view; its claimed latency was not benchmarked here. [Inspected source at 5c49f1a36d91](https://github.com/oakplank/gpt-image-bridge/blob/5c49f1a36d91ba411b5cec96859cd0a3f0a5c2dc/skills/gpt-image-bridge/bin/gpt-image-2).

### yuji-hatakeyama/opencode-gpt-imagegen

An OpenCode plugin that resolves OAuth authentication, calls the private Codex Responses endpoint, extracts an image-generation result, and saves a local file. Reference handling and clear one-result tool output are useful concepts. The adapter changes both the transport and destination relative to the requested normal UI flow, and its authentication implementation was not adopted. [Inspected source at 4ee90581b4f5](https://github.com/yuji-hatakeyama/opencode-gpt-imagegen/blob/4ee90581b4f541b9ba3b26c485dcbd09d3789087/src/codex.ts).

### faborsky/gpt-image-app

A public OpenAI Image API application with parameter validation, reference handling, local output, and retry/cost reporting. It distinguishes hard errors and policy refusals from transient errors and caps jittered delays. Reuse that distinction for clear observation failures. Do not transplant its generation retries to a browser after Send, or treat locally estimated cost/model metadata as evidence of this account’s quota or actual backend. [Inspected source at 6804de399bd0](https://github.com/faborsky/gpt-image-app/blob/6804de399bd09887b375d89b0c33ab1a2ad334f4/gptimage/generator.py).

### labeveryday/gpt-image-mcp

A FastMCP server backed by AsyncOpenAI. The server requires an API key and saves results through a temporary-file manager. Generation can fall back to another configured model after an error. Its tools separate image generation from local save failures, which is a useful outcome distinction. It does not implement this account’s Images UI or library verification; prompt optimization, branding, fallback, and cleanup were not imported. [Inspected source at 8a09c8d77e71](https://github.com/labeveryday/gpt-image-mcp/blob/8a09c8d77e717c94799ec81f458aeeefd6c8a2e6/src/gpt_image_mcp/image_generator.py).

### warshanks/gpt_image_pipe

An Open WebUI pipe using AsyncOpenAI and configured API keys. It emits base64 image output into its own chat interface. It keeps the latest relevant reference-image set and distinguishes partial previews from finished output. Those are useful future editing/observation concepts, but the pipe does not establish consumer ChatGPT library creation or subscription-funded access. No reference-upload support was added to the headless helper in this change. [Inspected source at 80156f6d0983](https://github.com/warshanks/gpt_image_pipe/blob/80156f6d098304800a585b9de760718b99455233/gpt_image_pipe.py).

### feedtailor/ccskill-gptimage

Supports Codex CLI and public API generation with local images, metadata sidecars, and a local gallery. Auto mode can fall back to the API after a Codex timeout or missing result when an API key exists, introducing another billable attempt without proving the earlier request was absent. Session-log/latest-file recovery also needs exact ownership under concurrency. Reuse provenance reporting; do not switch providers, quotas, or destinations during uncertain recovery. [Inspected source at bb7b8d2cab08](https://github.com/feedtailor/ccskill-gptimage/blob/bb7b8d2cab08dbf44272ffc8079d4fd254faee41/generate_image.py).

### Connected-Mate/gptimage

An MCP/CLI tool that resolves Codex/OpenCode-style credentials, posts to the private Codex Responses endpoint, extracts a streamed base64 result, and writes a PNG. Its output helper avoids overwriting existing local assets. That behavior is useful for local-file workflows, but it provides no observed library creation in this task. OAuth token reading/refresh and private transport were not used. [Inspected source at 49273481195c](https://github.com/Connected-Mate/gptimage/blob/49273481195c9795a4b6dc47b28549139c977025/src/codex.js).

### kymuco/gptty

A terminal wrapper around chatgpt-web-adapter with send, attach, messages, status, and wait operations. Keeping transport inside its SDK is a useful ownership model. It requires auth_data.json and documents an auth-capture path whose auto mode sends a probe. The image option primarily provides input images to prompts; generation and requested-library persistence are not established by the inspected wrapper. No auth capture or probe message was performed. [Inspected source at 93288f88ffba](https://github.com/kymuco/gptty/blob/93288f88ffbacf304f5bd161030e79fbf2ca486c/src/gptty/sdk_client.py).

### stufently/gpt-web-gateway

A real headless-capable browser gateway with serialized work, readiness checks, pacing, session health, and separate timing. Its Chrome mode copies a browser profile; its default mode loads saved storage state. It also hooks private backend responses and exports images. The UI Send-click catch can fall back to Enter after a click error. Reuse bounded observation and ownership ideas without copying sessions, adding backend hooks, or replaying uncertain dispatch. The installed dedicated browser runtime already satisfied the headless requirement. [Inspected source at d96998bd2848](https://github.com/stufently/gpt-web-gateway/blob/d96998bd28482d0d359d7fd75a6e878e504162d7/src/chatgpt.js).

## Gist

[Intellectronica’s GPT Image skill](https://gist.github.com/intellectronica/40d99adce3f6923fb875c0d39e810e0d)
was inspected at revision `71c4df61a30664c4b019aee6ded3c3044e3af4f3`. Its script uses the public
Responses API for generation and the Images API for edits, then saves a local PNG.
The CLI and error/result separation are reusable ideas. API credentials and local
output make it a different route; its suggestion to pass a key in chat was not
adopted. The archived installer and example PNG were not used. The new helper
imports its dependencies at module scope and reuses its installed environment.

## GitHub topics

[chatgpt-clone](https://github.com/topics/chatgpt-clone) and
[no-api-key](https://github.com/topics/no-api-key) were reviewed as discovery pages.
Coverage is the initial rendered page, not every repository or all pagination.
They contain heterogeneous tools and are not implementation contracts. A clone
UI or topic label alone provides no evidence of headless control, subscription
entitlement, or persistence in the user’s actual ChatGPT Images library. No broad
installation or unrequested repository expansion followed from these pages.

## Maintenance rules derived from the evidence

- Treat a submit timeout as an uncertain side effect. Preserve the request ID,
  exact prompt, conversation, and durable intent; observe before another action.
- Match actual rendered controls within the composer and actual image nodes
  within the owned turn. Locale-dependent labels need observed compatibility.
- Require loaded output and library identity. Keep image creation, export, and
  library persistence as separate outcomes; neither a title nor dimensions prove
  that the correct new library card exists.
- Reuse the existing browser owner and profile lock. Preserve active research and
  the service’s prior state instead of competing for a profile or copying it.
- Measure the actual stages. Recovery observation of an already completed image
  is not generation latency, and a single successful case is not a speed claim.
- Preserve the chosen product, account, destination, count, and prompt constraints.
  A fallback that changes billing or saves somewhere else needs its own authority
  and evidence. Never infer unlimited access or an image backend from a repo name.

Official references: [Playwright persistent browser contexts](https://playwright.dev/python/docs/api/class-browsertype#browser-type-launch-persistent-context),
[ChatGPT image generation](https://learn.chatgpt.com/docs/image-generation), and
[Library in ChatGPT](https://help.openai.com/en/articles/20001052-file-storage-and-library-in-chatgpt).
