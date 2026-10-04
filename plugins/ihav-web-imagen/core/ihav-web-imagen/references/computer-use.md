# Connected browser control

Use only APIs documented by the running Computer Use tool. On first use,
select/create the requested surface with one entry-point call and read its
returned documentation. After context recovery call `cua.rewriteDocumentation()`.

Choose a connected authenticated profile from inventory, respecting the user's
browser choice. A running Chrome native app does not establish a connection.

```javascript
// Only if Chrome is connected; use the user's requested browser if different.
var imagesTab = await cua.createBrowserTab(
  "chrome", "https://chatgpt.com/space/files?tab=images",
  {sessionName:"🎨 Images"}
);
```

This returns the initial state. Reuse the tab binding, its `id`, and the browser
identity. For a tab mention, bind the full mention URL with `cua.getTab` instead.
Use observed current indices, never numeric examples literally.

## App and Tab input methods differ

```javascript
// Browser Tab: the first argument identifies/focuses the editor.
await imagesTab.paste(editorIndex, prompt, {format:"text"});
await imagesTab.getAXState(); // verify draft and enabled Send before submitting

// Native App: no element argument on paste/pressKey/typeText.
await chromeApp.click(editorIndex);
await chromeApp.paste(prompt, {format:"text"});
await chromeApp.getAXState();
// Navigation, when the address bar is actually focused:
await chromeApp.pressKey("Return");
// Tab equivalent, only when its currently focused field needs Return:
await imagesTab.pressKey(null, "Return");
```

`setValue` may update an address field without focus. Focus the current field
explicitly before Return. For a rich editor prefer focused paste and verify the
accepted draft. Use the observed enabled Send control after recording intent.

Batch a known action and its `getAXState()` in one call. A New click changes the
page; inspect that state before targeting the editor. After a rerender or an
invalid-element error, derive fresh indices. Prefer AX diffs. If a native tree
contains unrelated tabs, use `{emit:false}` and emit only the relevant page
subtree, retaining the full current state locally for index selection.

Capture methods already wait; do not add sleeps before captures. Use screenshots
for image inspection or missing context. Keep pending generation on its owned
tab; open a second owned tab to verify Images.

## Missing connection

Follow [official setup](https://learn.chatgpt.com/docs/chrome-extension): ChatGPT
desktop **Settings → Computer Use**, select the intended browser, install/enable
its ChatGPT extension through the official store flow, confirm **Manage**, then
mention **@Chrome** (or the intended browser/tab) in the task. Use the profile
where the extension and ChatGPT login are available. Website access is managed
separately.

A denied assistant-app handle is a tool access boundary. Do not switch control
mechanisms to operate that denied app. Explain the missing connection and let
the user complete setup when that UI is inaccessible.

The built-in `@Browser` uses a separate profile and does not inherit Chrome
login automatically. The official built-in browser is unavailable in Codex
CLI/IDE; this does not determine availability of an authorized CLI browser bridge.
