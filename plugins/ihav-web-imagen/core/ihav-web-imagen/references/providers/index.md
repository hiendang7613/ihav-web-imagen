# Image provider cards

Documentation snapshot: **2026-10-04, Asia/Ho_Chi_Minh**. These cards describe documented web capabilities, not working adapters or provider permission.

- **C — Checked:** explicitly supported by the public sources reviewed on the snapshot date. This is document evidence, not a live account check.
- **I — Inferred:** interpretation of that evidence, with its basis stated.
- **U — Unverified:** unresolved by the reviewed sources. Unknown quotas do not mean unlimited use.

Login URLs below are application entry points inferred from the documented chat URL. Dedicated authentication routes, redirects and successful login remain **U**. No browser use, login, prompt submission or image download was performed to prepare these cards.

`Adapter: not built; live: unverified` records the starting state of the **provider-adapter workstream** in this snapshot. It does not describe or replace the existing ChatGPT-only integration. Update cards only when corresponding implementation and live evidence exist; a source announcement is insufficient.

| Site ID / card | Documented web generation | Model evidence | Access / limits |
| --- | --- | --- | --- |
| [chatgpt](chatgpt.md) | **C:** yes | **C:** ChatGPT Images; backend **U** | **C:** Free/paid; daily number **U** |
| [gemini](gemini.md) | **C:** yes | **C:** Nano Banana 2; paid Pro redo | **C:** free/paid; image daily number **U** |
| [lechat](lechat.md) | **C:** yes | **C:** historical FLUX Ultra; current variant **U** | **C:** logged-in Free daily cap; number **U** |
| [qwen](qwen.md) | **C:** yes | **C:** Qwen-Image family; selected version **U** | Current image plan / daily number **U** |
| [grok](grok.md) | **C:** yes | **C:** Imagine; backend **U** | **C:** paid access; free entitlement **U** |
| [perplexity](perplexity.md) | **C:** yes | **C:** GPT Image 1, Nano Banana, Seedream 4.5 | **C:** Free/Pro/Max; daily number **U** |
| [metaai](metaai.md) | **C:** yes | Exact image model **U** | **C:** free; daily number / account rollout **U** |
| [zai](zai.md) | **C:** partial, separate image app | **C:** GLM-Image app; chat integration **U** | Image-app plan / daily number **U** |
| [kimi](kimi.md) | **C:** partial, plugin-mediated | Plugin image backend **U**; K3 is chat routing evidence | Image-plugin entitlement / daily number **U** |

**I — Terms boundary:** documented features and account-owner approval do not establish provider permission for automation. Cards preserve restrictions and gaps; they are not a legal compliance determination. No provider is qualified for unrestricted automated access by this documentation.

**U — Scope exclusions:** no Claude or DeepSeek card is included in this adapter workstream. This index makes no additional capability claim about either provider.
