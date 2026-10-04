from __future__ import annotations

import asyncio
import os
import re
import sys
import time
from datetime import datetime, timezone
from collections import Counter
from pathlib import Path
from urllib.parse import urljoin, urlparse
from weakref import WeakKeyDictionary

from .core import ResearchError, RuntimeConfig, saved_url, digest
from .markdown import html_to_markdown
from .observation import Observation, TurnBinding

COMPOSER = '#prompt-textarea, [data-testid="prompt-textarea"], [contenteditable="true"][role="textbox"]'
SEND = 'button[data-testid="send-button"], button[aria-label="Send prompt"], button[aria-label="Send message"]'
STOP = 'button[data-testid="stop-button"], button[aria-label="Stop streaming"], button[aria-label="Stop generating"]'
PICKER = 'button[data-testid="model-switcher-dropdown-button"], button.__composer-pill[aria-haspopup="menu"]'
USER = '[data-message-author-role="user"]'
ASSISTANT = '[data-message-author-role="assistant"]'


# One rendered snapshot owns identity, content, links and completion evidence. The
# array positions below describe this DOM snapshot only; they are never durable IDs.
OBSERVE_DOM = r"""({marker}) => {
    const ROLE = '[data-message-author-role="user"],[data-message-author-role="assistant"]';
    const TURN = '[data-testid^="conversation-turn-"],article,[data-turn-id],[data-turn-id-container]';
    const BLOCK = 'p,h1,h2,h3,h4,h5,h6,pre,table,ul,ol,blockquote,hr,figure,details';
    const PROSE = 'p,h1,h2,h3,h4,h5,h6,pre,code,table,ul,ol,li,blockquote';
    const statusIds = new Set(['thinking-status', 'thought-status', 'response-status']);
    const norm = text => (text || '').replace(/\s+/g, ' ').trim();
    const visible = node => {
        for (let current = node; current; current = current.parentElement) {
            const style = getComputedStyle(current);
            // KaTeX's painted branch is intentionally hidden from screen readers.
            const hiddenMath = current.getAttribute('aria-hidden') === 'true' &&
                current.matches('.katex-html') && current.closest('.katex');
            if (current.hidden || current.hasAttribute('inert') ||
                (current.getAttribute('aria-hidden') === 'true' && !hiddenMath) ||
                style.display === 'none' || style.visibility === 'hidden' ||
                style.visibility === 'collapse' || style.contentVisibility === 'hidden' ||
                Number(style.opacity) === 0) return false;
        }
        return node.getClientRects().length > 0 || [...node.children].some(visible);
    };
    const identitiesOf = node => ['data-turn-id', 'data-turn-id-container'].map(
        attr => node.getAttribute(attr)).filter(Boolean);
    const boundary = node => {
        let current = node.closest(TURN) || node;
        const identities = new Set(identitiesOf(current));
        if (identities.size !== 1) return current;
        const identity = [...identities][0];
        let parent = current.parentElement?.closest(TURN);
        // The donor renders one logical turn as nested wrappers with the same
        // explicit ID. Collapse only that chain; separate duplicates stay separate.
        while (parent) {
            const parentIds = new Set(identitiesOf(parent));
            if (parentIds.size !== 1 || !parentIds.has(identity)) break;
            current = parent;
            parent = current.parentElement?.closest(TURN);
        }
        return current;
    };
    const copyRenderer = node => node.tagName === 'BUTTON' && (
        ['copy-turn-action-button', 'copy-code-button'].includes(node.getAttribute('data-testid')) ||
        ['aria-label', 'title'].some(attr => ['copy', 'copy code', 'copy response'].includes(
            norm(node.getAttribute(attr)).toLowerCase())));
    const copyResponse = node => node.tagName === 'BUTTON' && (
        node.getAttribute('data-testid') === 'copy-turn-action-button' ||
        norm(node.getAttribute('aria-label')).toLowerCase() === 'copy response');
    const structuralStatus = node => node.getAttribute('role') === 'status' ||
        statusIds.has(node.getAttribute('data-testid'));
    const errorControl = node => node.tagName === 'BUTTON' &&
        node.getAttribute('data-testid') === 'regenerate-thread-error-button';
    const citationButton = node => node.tagName === 'BUTTON' && (
        node.hasAttribute('data-citation-id') || node.hasAttribute('data-citation') ||
        ['citation', 'citation-button', 'inline-citation'].includes(node.getAttribute('data-testid')));
    const textOf = node => {
        if (node.nodeType === Node.TEXT_NODE) return node.textContent;
        if (node.nodeType !== Node.ELEMENT_NODE) return '';
        if (node.tagName === 'BR') return '\n';
        if (node.tagName === 'IMG') return node.getAttribute('alt') || '';
        const text = [...node.childNodes].map(textOf).join('');
        return node.matches(BLOCK + ',div,section,article,li,tr') ? '\n' + text + '\n' :
            node.matches('td,th') ? text + '\t' : text;
    };
    const plainText = node => node.matches('pre') ? [...node.childNodes].map(textOf).join('') :
        textOf(node).replace(/[ \t]+\n/g, '\n').replace(/\n{3,}/g, '\n\n').trim();
    const codeViewerProjection = outer => {
        if (!outer.matches('pre')) return null;
        const candidates = [...outer.querySelectorAll('[id="code-block-viewer"]')].filter(
            viewer => viewer.closest('pre') === outer);
        if (!candidates.length) return null;
        const viewers = candidates.filter(visible);
        if (viewers.length !== 1) return {unresolved: true};
        const viewer = viewers[0];
        const payloads = [...viewer.querySelectorAll('pre.cm-content > code')].filter(code =>
            code.closest('[id="code-block-viewer"]') === viewer && visible(code));
        if (payloads.length !== 1) return {unresolved: true};
        const payload = payloads[0];
        const languages = nodes => [...new Set(nodes.flatMap(node => [...node.classList].flatMap(value => {
            const match = /^language-([A-Za-z0-9][A-Za-z0-9_+.#-]*)$/.exec(value);
            return match ? [match[1]] : [];
        })))];
        let explicit = languages([payload]);
        if (!explicit.length) explicit = languages([payload.parentElement, outer]);
        let language = explicit.length === 1 ? explicit[0] : '';
        if (!explicit.length) {
            // These two exact header labels and this Copy/header placement are
            // proved by the saved rendered Markdown and HTML code viewers.
            const headers = [...outer.querySelectorAll('.select-none.sticky')].filter(header =>
                visible(header) && !viewer.contains(header) &&
                (header.compareDocumentPosition(viewer) & Node.DOCUMENT_POSITION_FOLLOWING) &&
                [...header.querySelectorAll('button')].filter(button => visible(button) && copyRenderer(button)).length === 1);
            const labels = headers.length === 1 ? [...headers[0].querySelectorAll('.justify-self-start')].filter(visible) : [];
            if (labels.length === 1) language = new Map([['Markdown', 'markdown'], ['HTML', 'html']]).get(norm(labels[0].innerText)) || '';
        }
        return {payload, language};
    };
    const cloneRendered = (node, exclude = () => false) => {
        if (node.nodeType === Node.TEXT_NODE) return node.cloneNode();
        if (node.nodeType !== Node.ELEMENT_NODE || !visible(node) || exclude(node) ||
            node.matches('script,style,noscript,template')) return null;
        const codeViewer = codeViewerProjection(node);
        if (codeViewer) {
            if (codeViewer.unresolved) return null;
            const code = cloneRendered(codeViewer.payload, exclude);
            if (!code) return null;
            if (codeViewer.language) code.classList.add('language-' + codeViewer.language);
            const pre = document.createElement('pre');
            pre.appendChild(code);
            return pre;
        }
        const clone = node.cloneNode(false);
        if (node.matches('a[href]')) clone.setAttribute('href', node.href);
        for (const child of node.childNodes) {
            const copied = cloneRendered(child, exclude);
            if (copied) clone.appendChild(copied);
        }
        return clone;
    };
    const semanticFragments = root => {
        if (root.matches(BLOCK + ',.katex-display')) return [root];
        const fragments = [];
        let inline = document.createElement('div');
        const flush = () => {
            if (inline.childNodes.length) fragments.push(inline);
            inline = document.createElement('div');
        };
        const visit = node => {
            if (node.nodeType === Node.ELEMENT_NODE &&
                (node.matches(BLOCK) || node.matches('.katex-display'))) {
                flush(); fragments.push(node); return;
            }
            if (node.nodeType === Node.ELEMENT_NODE && node.querySelector(BLOCK + ',.katex-display')) {
                flush(); for (const child of [...node.childNodes]) visit(child); flush();
            } else inline.appendChild(node.cloneNode(true));
        };
        for (const child of [...root.childNodes]) visit(child);
        flush();
        return fragments;
    };
    const roles = [...document.querySelectorAll(ROLE)].filter(node => visible(node) &&
        !node.parentElement?.closest(ROLE));
    const containers = new Set([...document.querySelectorAll(TURN)].filter(node => {
        const role = node.closest(ROLE);
        return !role || (role === node && boundary(role) === node);
    }).map(boundary));
    for (const role of roles) containers.add(boundary(role));
    const turns = [...containers].sort((left, right) => left === right ? 0 :
        left.compareDocumentPosition(right) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1);
    return {
        generating: [...document.querySelectorAll(
            'button[data-testid="stop-button"],button[aria-label="Stop streaming"],button[aria-label="Stop generating"]'
        )].some(visible),
        turns: turns.map(turn => {
            const ownedRoles = roles.filter(role => boundary(role) === turn);
            const users = ownedRoles.filter(role => role.getAttribute('data-message-author-role') === 'user');
            const assistants = ownedRoles.filter(role => role.getAttribute('data-message-author-role') === 'assistant');
            // Explicit logical attributes are evidence. DOM ids and display indices are not.
            const identities = identitiesOf(turn);
            const preferred = assistants.flatMap(role => [...role.querySelectorAll('.markdown'),
                ...(role.matches('.markdown') ? [role] : [])].filter(node => visible(node) &&
                    !node.closest('pre,code,blockquote,[role="status"],[data-testid="thinking-status"],' +
                        '[data-testid="thought-status"],[data-testid="response-status"]')));
            const markdownRoots = preferred.filter(node => !preferred.some(other => other !== node && other.contains(node)));
            // Markdown is a renderer wrapper. Structural UI children stay UI,
            // while status-like elements quoted inside actual prose stay content.
            const outsideProse = node => !node.closest('pre,code,blockquote') &&
                !assistants.some(role => role.contains(node) && node.parentElement?.closest(PROSE) &&
                    role.contains(node.parentElement.closest(PROSE)));
            const statusRoot = node => (structuralStatus(node) || errorControl(node)) && outsideProse(node);
            const exclude = node => copyRenderer(node) || statusRoot(node);
            const roots = assistants.flatMap(role => {
                const selected = markdownRoots.filter(root => role.contains(root));
                return selected.length ? selected : [role];
            });
            let unproven = assistants.some(role => [...role.querySelectorAll('pre')].some(
                pre => visible(pre) && codeViewerProjection(pre)?.unresolved));
            for (const role of assistants) {
                const selected = markdownRoots.filter(root => role.contains(root));
                if (!selected.length || selected.includes(role)) continue;
                const extra = cloneRendered(role, node => selected.includes(node) || exclude(node));
                if (extra && (plainText(extra) || extra.querySelector('img,video,audio,canvas,svg,math'))) unproven = true;
            }
            const cleanedRoots = roots.map(root => cloneRendered(root, exclude)).filter(Boolean);
            const fragments = cleanedRoots.flatMap(semanticFragments);
            const links = cleanedRoots.flatMap(root => [...root.querySelectorAll('a[href]')].map(anchor => ({
                title: norm(textOf(anchor)) || anchor.getAttribute('aria-label') || '',
                url: anchor.getAttribute('href'),
            })));
            const fileChip = '[data-file-citation-group-identity][data-file-citation-primary-file-id]';
            const unresolvedCitations = cleanedRoots.reduce((count, root) => count +
                [...root.querySelectorAll('button')].filter(node => citationButton(node) &&
                    !node.closest(fileChip) && !node.closest('pre,code') && !node.querySelector('a[href]')).length +
                [...root.querySelectorAll(fileChip)].filter(node => node.querySelector('button') &&
                    !node.parentElement?.closest(fileChip) && !node.closest('pre,code') &&
                    !node.querySelector('a[href]')).length, 0);
            const controls = [...turn.querySelectorAll('button,[role="status"],[data-testid]')].filter(node =>
                visible(node) && boundary(node) === turn && outsideProse(node));
            const terminal = controls.some(errorControl) ? 'response_error' :
                controls.some(node => structuralStatus(node) && norm(node.innerText) === 'Stopped thinking') ?
                    'stopped_thinking' : null;
            const finalControl = roots.length > 0 && controls.some(node => copyResponse(node) &&
                !roots.some(root => root.contains(node)) &&
                roots.every(root => root.compareDocumentPosition(node) & Node.DOCUMENT_POSITION_FOLLOWING));
            return {
                identity: identities[0] || null,
                identity_conflict: new Set(identities).size > 1,
                visible: visible(turn),
                roles: ownedRoles.map(role => role.getAttribute('data-message-author-role')),
                marker_count: users.filter(role => {
                    const clone = cloneRendered(role);
                    return clone && plainText(clone).split('\n').includes(marker);
                }).length,
                response_present: assistants.length > 0 || terminal !== null,
                fragments: fragments.map(fragment => ({html: fragment.outerHTML, text: plainText(fragment)})),
                links, unresolved_citations: unresolvedCitations, final_control: finalControl,
                terminal_reason: terminal, projection_unproven: unproven,
            };
        }),
    };
}"""


async def visible_controls(locator):
    found = []
    for i in range(await locator.count()):
        node = locator.nth(i)
        if await node.is_visible() and await node.evaluate(
            "e => !e.closest('[inert], [aria-hidden=\"true\"]')"
        ):
            found.append(node)
    return found


async def first_visible(locator):
    nodes = await visible_controls(locator)
    return nodes[0] if nodes else None


async def required(locator, code: str, *, timeout=8.0, hit=False):
    deadline = time.monotonic() + timeout
    while True:
        nodes = await visible_controls(locator)
        if hit:
            if len(nodes) == 1 and await nodes[0].evaluate("""e=>{
                const r=e.getBoundingClientRect();
                return r.bottom<=0 || r.top>=innerHeight || r.right<=0 || r.left>=innerWidth;
            }"""):
                await nodes[0].scroll_into_view_if_needed(timeout=max(1, int((deadline-time.monotonic())*1000)))
            nodes = [n for n in nodes if await n.evaluate("""e => {
                const r=e.getBoundingClientRect(), h=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);
                return h===e || e.contains(h);
            }""")]
        if len(nodes) == 1:
            return nodes[0]
        if time.monotonic() >= deadline:
            raise ResearchError(code, "Required ChatGPT control is missing, obscured or ambiguous")
        await asyncio.sleep(0.1)


def normalized(value: str) -> str:
    return " ".join(value.split())


def effort_value(value: str) -> str | None:
    # Semantic name is required. Position and punctuation are presentation, never the tier.
    match = re.fullmatch(r"(Instant|Medium|High|Extra High|Pro)(?:\s*,?\s*\d+\s+of\s+\d+)?[.!]?",
                         normalized(value), re.I)
    return match.group(1).title() if match else None


def exact_label(label: str):
    return re.compile(r"^" + re.escape(label) + r"(?:\s*[✓✔]|\s+selected)?$", re.I)


def cloak_binary(config: RuntimeConfig) -> Path:
    """The configured Chromium, else the one `imagine.py setup` downloaded. Never downloads during a run."""
    if config.browser_binary:
        binary = Path(config.browser_binary).expanduser()
    else:
        from cloakbrowser.config import get_binary_path
        binary = get_binary_path()
    if not binary.is_file():
        raise ResearchError("browser_binary_missing", "The CloakBrowser Chromium is not installed; run `imagine.py setup` (it sends nothing)")
    return binary


class Browser:
    """One persistent context. Calls that change composer settings are serialized by Worker."""

    upload_timeout_seconds = 120
    cleanup_timeout_seconds = 5

    def __init__(self, state: Path, config: RuntimeConfig):
        self.state, self.config = state, config
        self.context = None
        self.prepared_attachments = WeakKeyDictionary()

    async def start(self, *, headless: bool | None = None, accept_downloads: bool = False):
        os.environ["CLOAKBROWSER_BINARY_PATH"] = str(cloak_binary(self.config))
        os.environ["CLOAKBROWSER_AUTO_UPDATE"] = "false"
        from cloakbrowser import launch_persistent_context_async
        self.context = await launch_persistent_context_async(
            user_data_dir=str(self.state / "profile"),
            headless=self.config.headless if headless is None else headless,
            viewport={"width": 1440, "height": 1100},
            accept_downloads=accept_downloads,
        )
        self.context.set_default_timeout(8000)
        self.context.set_default_navigation_timeout(45000)
        return self

    async def close(self):
        if self.context:
            try:
                async with asyncio.timeout(self.cleanup_timeout_seconds):
                    await self.context.close()
            finally:
                self.context = None
                self.prepared_attachments.clear()

    async def open(self, url: str | None = None):
        if url:
            saved_url(url)
        page = await self.context.new_page()
        try:
            await page.goto(url or "https://chatgpt.com/", wait_until="domcontentloaded")
            await self.check_access(page)
            await page.locator(COMPOSER).first.wait_for(state="visible", timeout=30000)
            await self.check_access(page)
            return page
        except BaseException as original:
            try:
                async with asyncio.timeout(self.cleanup_timeout_seconds):
                    await page.close()
            except Exception as cleanup:
                original.add_note(f"page_cleanup_failed: {type(cleanup).__name__}")
            raise

    async def check_access(self, page, *, strict_alerts=True):
        async def access_control(locator):
            for node in await visible_controls(locator):
                if strict_alerts or await node.evaluate(
                    "e => !e.closest('[data-message-author-role]')"
                ):
                    return node
            return None
        login = await access_control(page.get_by_role("button", name=re.compile(r"^(Log in|Sign in)$", re.I)))
        if login:
            raise ResearchError("login_required", "Sign in manually using the dedicated worker profile")
        challenge = await access_control(page.get_by_text(
            re.compile(r"^(Verify you are human|Checking your browser|Just a moment)", re.I)
        ))
        if challenge:
            raise ResearchError("human_verification_required", "Manual browser verification is needed")
        for alert in await visible_controls(page.locator('[role="alert"]')):
            text = normalized(await alert.inner_text())
            if strict_alerts and text:
                raise ResearchError("chatgpt_alert", "ChatGPT displays an alert; review the existing UI before continuing")
            if not strict_alerts and await alert.evaluate("e => !e.closest('[data-message-author-role]')") and re.fullmatch(
                r"(?:Your session has expired[.!]?|Session expired[.!]?|Please (?:log|sign) in again[.!]?)", text, re.I
            ):
                raise ResearchError("login_required", "Sign in manually using the dedicated worker profile")

    async def composer(self, page):
        return await required(page.locator(COMPOSER), "composer_unavailable")

    async def composer_scope(self, page):
        composer = await self.composer(page)
        # Deliberately avoid whole-page text: a prior answer can mention Web search or file names.
        for selector in ('xpath=ancestor::form[1]', 'xpath=ancestor::*[@data-testid="composer"][1]'):
            scope = composer.locator(selector)
            if await scope.count():
                return scope.first
        raise ResearchError("composer_scope_unavailable", "Cannot identify the composer boundary")

    async def chat_mode(self, page, *, select=False):
        controls = page.locator('[data-tpp-toggle-value="chatgpt"], [role="radio"][aria-label="Chat"]')
        if not await controls.count():
            controls = page.get_by_role("radio", name="Chat", exact=True)
        chat = await required(controls, "chat_mode_unverified")
        if select and not await self.checked(chat):
            await chat.click()
        if not await self.checked(chat):
            raise ResearchError("chat_mode_unverified", "Chat mode is not selected")
        return {"label": "Chat", "evidence": "checked_mode_control"}

    async def open_picker(self, page):
        await page.keyboard.press("Escape")
        await page.keyboard.press("Escape")
        control = await required(page.locator(PICKER), "model_picker_unavailable", hit=True)
        await control.click()
        return await self.picker_scope(page)

    async def picker_scope(self, page):
        root = page.get_by_test_id("composer-intelligence-picker-content")
        if await root.count():
            return await required(root, "model_menu_unavailable")
        return await required(page.get_by_role("menu"), "model_menu_unavailable")

    async def option(self, scope, label):
        candidates = None
        for role in ("menuitemradio", "radio", "option", "menuitem", "button"):
            part = scope.get_by_role(role, name=exact_label(label))
            candidates = part if candidates is None else candidates.or_(part)
        nodes = await visible_controls(candidates)
        if len(nodes) > 1:
            raise ResearchError("ambiguous_option", "Multiple accessible options match the requested label")
        return nodes[0] if nodes else None

    async def checked(self, control):
        return any([
            await control.get_attribute("aria-checked") == "true",
            await control.get_attribute("aria-selected") == "true",
            await control.get_attribute("data-state") == "checked",
        ])

    async def model_menu(self, page):
        scope = await self.open_picker(page)
        submenu = await self.option(scope, "Select model")
        if submenu:
            await submenu.click()
            scope = await self.picker_scope(page)
        # Wait on the current panel becoming interactive, not a snapshot of its pre-animation count.
        deadline = time.monotonic() + 8
        while not await visible_controls(scope.locator('[role="menuitemradio"], [role="option"], [role="radio"]')):
            if time.monotonic() >= deadline:
                raise ResearchError("model_options_unavailable", "Model options did not become ready")
            await asyncio.sleep(0.1)
        return scope

    async def power_control(self, scope):
        control = scope.get_by_role("menuitem", name=re.compile(r"^(Power|Thinking effort)$", re.I)).or_(
            scope.get_by_role("slider", name=re.compile(r"^(Power|Thinking effort)$", re.I)))
        return await required(control, "effort_control_unavailable")

    async def read_power(self, control):
        values = await control.evaluate(r"""e => {
            const root=e.closest('[role="menu"]') || e.parentElement;
            const values=[e.getAttribute('aria-valuetext')];
            for(const id of (e.getAttribute('aria-describedby')||'').split(/\s+/)) {
                const node=document.getElementById(id);
                if(node && root.contains(node)) values.push(node.textContent);
            }
            return values.filter(Boolean);
        }""")
        pairs = [(effort_value(v), normalized(v)) for v in values if effort_value(v)]
        if not pairs or len({name for name, _ in pairs}) != 1:
            raise ResearchError("effort_unverified", "Power has no unambiguous semantic selected value")
        label, observed = pairs[0]
        return {"label": label, "effective": label, "evidence": "power_accessible_value", "observed": observed}

    async def read_configuration(self, page):
        mode = await self.chat_mode(page)
        trigger = await required(page.locator(PICKER), "model_picker_unavailable", hit=True)
        displayed = normalized(await trigger.inner_text())
        try:
            scope = await self.model_menu(page)
            selected = []
            for node in await visible_controls(scope.locator('[role="menuitemradio"], [role="option"], [role="radio"]')):
                if await self.checked(node):
                    selected.append(normalized(await node.inner_text()))
            if len(selected) != 1:
                raise ResearchError("model_unverified", "Expected one checked model in the model menu")
            offered = selected[0]
            six = bool(re.fullmatch(r"(?:GPT[- ]?)?6(?: (?:Instant|Medium|High|Extra High|Pro))?", displayed, re.I))
            effective = "GPT-6" if six and offered in {"Latest", "GPT-6", "6"} else offered
            if offered == "Latest" and not six:
                effective = None
            model = {"label": effective, "effective": effective, "selected_option": offered,
                     "displayed": displayed, "evidence": "checked_model_and_trigger" if effective else "checked_alias_only"}
            scope = await self.open_picker(page)
            effort = await self.read_power(await self.power_control(scope))
            return {"mode": mode, "model": model, "effort": effort}
        finally:
            await page.keyboard.press("Escape")
            await page.keyboard.press("Escape")

    async def select_model(self, page, label: str) -> dict:
        await self.chat_mode(page, select=True)
        scope = await self.model_menu(page)
        option = await self.option(scope, label)
        if option is None and label == "GPT-6":
            option = await self.option(scope, "6") or await self.option(scope, "Latest")
        if option is None:
            await page.keyboard.press("Escape")
            raise ResearchError("model_unavailable", "Requested model was not offered")
        if not await self.checked(option):
            await option.click()
        await page.keyboard.press("Escape")
        selected = (await self.read_configuration(page))["model"]
        # Latest's effective identity can appear only after selecting Pro. Final confirmation is mandatory.
        alias_pending = label == "GPT-6" and selected["selected_option"] == "Latest" and selected["effective"] is None
        if selected["effective"] != label and not alias_pending and not (label == "Latest" and selected["selected_option"] == "Latest"):
            raise ResearchError("model_unverified", "Checked option does not prove the requested model")
        return {**selected, "requested": label}

    async def select_effort(self, page, label: str) -> dict:
        scope = await self.open_picker(page)
        control = await self.power_control(scope)
        await control.focus()
        # Current Power handles arrows, not Home/End. Bound exploration by the observed numeric range;
        # acceptance always uses the semantic value, including after closing and reopening the picker.
        bounds = await control.evaluate("""e=>{
            const slider=e.matches('[role="slider"]')?e:e.querySelector('[role="slider"]');
            return slider ? [slider.getAttribute('aria-valuemin'),slider.getAttribute('aria-valuemax')] : null;
        }""")
        try:
            count = int(float(bounds[1])) - int(float(bounds[0])) + 1 if bounds else 0
        except (ValueError, TypeError):
            count = 0
        if not 1 <= count <= 20:
            raise ResearchError("effort_unverified", "Power's bounded range is unavailable")
        current = await self.read_power(control)
        if current["label"].casefold() != label.casefold():
            for key in ["ArrowLeft"] * (count - 1) + ["ArrowRight"] * (count - 1):
                before = (await self.read_power(control))["label"]
                await control.press(key)
                deadline = time.monotonic() + 0.6
                while time.monotonic() < deadline:
                    current = await self.read_power(control)
                    if current["label"] != before:
                        break
                    await asyncio.sleep(0.05)
                if current["label"].casefold() == label.casefold():
                    break
        await page.keyboard.press("Escape")
        selected = (await self.read_configuration(page))["effort"]
        if selected["label"].casefold() != label.casefold():
            raise ResearchError("effort_unverified", "Requested effort was not confirmed by the UI")
        return {**selected, "requested": label}

    async def confirm_configuration(self, page, model: str, effort: str):
        selected = await self.read_configuration(page)
        actual = selected["model"]
        if (actual["effective"] != model and not (model == "Latest" and actual["selected_option"] == "Latest")) or \
                selected["effort"]["label"].casefold() != effort.casefold():
            raise ResearchError("configuration_changed", "Model or effort changed before sending")
        selected["model"]["requested"] = model
        selected["effort"]["requested"] = effort
        return selected

    async def probe_configuration(self, page, model, effort):
        await self.select_model(page, model)
        await self.select_effort(page, effort)
        return {**await self.confirm_configuration(page, model, effort), "prompt_sent": False,
                "scope": "UI selection and confirmation only; no generation"}

    async def tool_nodes(self, page):
        scope = await self.composer_scope(page)
        selector = (
            '[data-inline-selection-pill][data-id="search"][data-symbol="ecosystemMention"][data-system-hint-type="search"], '
            '[data-type="mention"], [data-testid="tool-chip"], button[aria-label="Remove Web search"], '
            'button[aria-label="Web search"], [data-testid="web-search-pill"]'
        )
        owned = []
        for node in await visible_controls(scope.locator(selector)):
            text = normalized(await node.inner_text())
            aria = await node.get_attribute("aria-label") or ""
            if text in {"Web search", "@Web search"} or aria in {"Remove Web search", "Web search"}:
                owned.append(node)
        return owned

    async def tool_selected(self, page) -> bool:
        return bool(await self.tool_nodes(page))

    async def select_tool(self, page):
        if await self.tool_selected(page):
            return
        composer = await self.composer(page)
        # ProseMirror normalization can leave the selection inside the final word after fill().
        # Move it with the native editing key before the tool inserts its inline pill and spacer.
        await composer.press("Meta+ArrowDown" if sys.platform == "darwin" else "Control+End")
        at_end = await composer.evaluate("""e=>{
            if(e.tagName==='TEXTAREA') return e.selectionStart===e.value.length && e.selectionEnd===e.value.length;
            const s=window.getSelection();
            if(!s || !s.isCollapsed || !s.rangeCount || !e.contains(s.anchorNode)) return false;
            const tail=document.createRange();tail.selectNodeContents(e);
            tail.setStart(s.anchorNode,s.anchorOffset);
            return tail.toString()==='';
        }""")
        if not at_end:
            raise ResearchError("composer_cursor_unverified", "Tool insertion must start at the end of the prompt")
        scope = await self.composer_scope(page)
        button = await required(scope.locator('button[data-testid="composer-plus-btn"]').or_(
            scope.get_by_role("button", name=re.compile(r"^(Add files and more|Add files and tools|Add|Tools)$", re.I))),
            "web_search_picker_unavailable", hit=True)
        await button.click()
        deadline = time.monotonic() + 8
        option = None
        while time.monotonic() < deadline:
            # The current composer popover has no menu role. Its owned, focusable row contains the exact label.
            popups = page.locator('[role="menu"], .popover[aria-busy="false"]')
            for popup in await visible_controls(popups):
                option = await self.option(popup, "Web search")
                if option is None:
                    rows = popup.locator('div.__menu-item[tabindex="0"]').filter(
                        has=page.get_by_text("Web search", exact=True))
                    options = await visible_controls(rows)
                    if len(options) > 1:
                        raise ResearchError("ambiguous_option", "Web search menu contains duplicate rows")
                    option = options[0] if options else None
                if option is not None:
                    break
            if option is not None:
                break
            await asyncio.sleep(0.1)
        if option is None:
            await page.keyboard.press("Escape")
            raise ResearchError("web_search_unavailable", "Web search was not offered")
        await option.click()
        while time.monotonic() < deadline:
            if await self.tool_selected(page):
                return
            await asyncio.sleep(0.1)
        raise ResearchError("web_search_unverified", "Web search was not confirmed in the composer")

    async def prompt_text(self, page):
        composer = await self.composer(page)
        if await composer.evaluate("e => e.tagName === 'TEXTAREA'"):
            return await composer.input_value()
        # Only exclude actual selected tool DOM nodes; literal 'Web search' in the prompt is untouched.
        owned = [await node.element_handle() for node in await self.tool_nodes(page)]
        return await composer.evaluate(r"""(e, owned) => {
            const walk = n => {
                if(owned.includes(n)) return '';
                // A pill at the start of an empty paragraph has an adjacent, hidden caret anchor.
                // Exclude that owned UI node, never U+FEFF appearing in the supplied prompt itself.
                if(n.nodeType===Node.ELEMENT_NODE &&
                   n.matches('[data-inline-selection-pill-cursor-target][aria-hidden="true"][contenteditable="false"]') &&
                   n.textContent==='\uFEFF' && owned.includes(n.nextElementSibling)) return '';
                if(n.nodeType===Node.TEXT_NODE) return n.textContent;
                if(n.nodeName==='BR') return n.classList.contains('ProseMirror-trailingBreak') ? '' : '\n';
                const parts=Array.from(n.childNodes).map(walk).join('');
                return ['P','DIV','LI'].includes(n.nodeName) && n!==e ? parts+'\n' : parts;
            };
            return walk(e);
        }""", owned)

    async def attachment_tiles(self, page) -> list[dict]:
        scope = await self.composer_scope(page)
        tiles = []
        # Owned attachment controls, not prompt prose or filenames in an older answer.
        for node in await visible_controls(scope.get_by_role(
                "button", name=re.compile(r"^Remove (?:file|attachment)\b"))):
            item = await node.evaluate(r"""e=>{
                const match=/^Remove file [1-9]\d*: (.+)$/.exec(e.getAttribute('aria-label')||'');
                if(!match) return {name:null,ready:false,reason:'unrecognized_attachment_control'};
                const name=match[1],group=e.closest('[role="group"]');
                const actions=group ? Array.from(group.querySelectorAll('[data-default-action="true"] button'))
                    .filter(b=>b.getAttribute('aria-label')===name) : [];
                // Send may be enabled before upload starts. The owned tile remains cursor-wait
                // until upload/processing and library collision renaming finish.
                const ready=group?.getAttribute('aria-label')===name && actions.length===1 &&
                    !actions[0].disabled && !actions[0].classList.contains('cursor-wait') &&
                    getComputedStyle(actions[0]).cursor!=='wait';
                return {name,ready:Boolean(ready),group_label:group?.getAttribute('aria-label')||null,
                    action_names:actions.map(b=>b.getAttribute('aria-label')),
                    action_disabled:actions.map(b=>b.disabled),
                    waiting:actions.map(b=>b.classList.contains('cursor-wait') || getComputedStyle(b).cursor==='wait')};
            }""")
            if item:
                tiles.append(item)
        return tiles

    async def attachment_names(self, page, *, ready_only=False) -> list[str]:
        tiles = await self.attachment_tiles(page)
        if any(not isinstance(tile["name"], str) for tile in tiles):
            raise ResearchError("upload_identity_unverified", "A composer attachment control could not be identified")
        return [tile["name"] for tile in tiles
                if not ready_only or tile["ready"]]

    async def empty_attachment_inventory(self, page):
        # Draft attachments can belong to the user or a prior interrupted upload.
        # Never delete them, or allow upload-delta evidence to authorize their Send.
        if await self.attachment_tiles(page):
            raise ResearchError("composer_attachments_present",
                                "The composer contains existing attachments; the draft was preserved")

    async def verify_attachment_inventory(self, page, attachments):
        tiles = await self.attachment_tiles(page)
        present = Counter(tile["name"] for tile in tiles)
        expected = Counter(attachment["displayed_filename"] for attachment in attachments)
        if present != expected:
            if expected - present:
                raise ResearchError("upload_lost", "An approved attachment disappeared before sending")
            raise ResearchError("upload_identity_unverified",
                                "The full composer attachment inventory differs from the approved files")
        if any(not tile["ready"] for tile in tiles):
            raise ResearchError("upload_not_ready", "A composer attachment is not verified ready")
        scope = await self.composer_scope(page)
        if await first_visible(scope.locator('[role="progressbar"], [aria-busy="true"]')):
            raise ResearchError("upload_not_ready", "An attachment is still busy")

    async def upload_diagnostic(self, page, paths, before):
        scope = await self.composer_scope(page)
        tiles = await self.attachment_tiles(page)
        names = [tile["name"] for tile in tiles]
        ready = list((Counter(tile["name"] for tile in tiles if tile["ready"]) - before).elements())
        send = await first_visible(page.locator(SEND))
        return {
            "expected_count": len(paths), "expected_names": [p.name for p in paths],
            "observed_names": list((Counter(names) - before).elements()), "ready_count": len(ready),
            "ready_names": ready, "tiles": tiles,
            "busy": await first_visible(scope.locator('[role="progressbar"], [aria-busy="true"]')) is not None,
            "send_visible": send is not None, "send_enabled": bool(send and await send.is_enabled()),
            "file_input_count": await page.locator('input[type="file"]').count(),
        }

    async def upload(self, page, paths: list[Path]) -> list[dict]:
        if len(paths) > 20:
            raise ResearchError("attachment_limit_exceeded", "Pack sources into at most 20 attachments before uploading")
        if not paths:
            return []
        scope = await self.composer_scope(page)
        file_input = page.locator('input[type="file"]').first
        if not await file_input.count():
            raise ResearchError("upload_unavailable", "Attachment input was not found")
        before = Counter(await self.attachment_names(page))
        if sum(before.values()) + len(paths) > 20:
            raise ResearchError("attachment_limit_exceeded", "Composer would contain more than 20 attachments")
        await file_input.set_input_files([str(p) for p in paths])
        end = time.monotonic() + self.upload_timeout_seconds
        while time.monotonic() < end:
            errors = scope.locator('[role="alert"]')
            if await errors.count() and (await errors.first.inner_text()).strip():
                raise ResearchError("upload_failed", "Composer reported an attachment error")
            added = list((Counter(await self.attachment_names(page, ready_only=True)) - before).elements())
            busy = await first_visible(scope.locator('[role="progressbar"], [aria-busy="true"]'))
            send = await first_visible(page.locator(SEND))
            if len(added) >= len(paths) and not busy and send and await send.is_enabled():
                attachments = []
                for path in paths:
                    # ChatGPT's file library can rename a repeat upload to stem(2).ext.
                    # Match only newly added controls and keep the effective display name separately.
                    collision = re.compile(re.escape(path.stem) + r"\([1-9]\d*\)" + re.escape(path.suffix))
                    candidates = [name for name in added if name == path.name or collision.fullmatch(name)]
                    if len(candidates) != 1:
                        raise ResearchError("upload_identity_unverified", "New attachment names do not identify the selected files uniquely")
                    attachments.append({"filename": path.name, "displayed_filename": candidates[0],
                                        "evidence": "new_composer_attachment_and_ready_send"})
                if len(added) != len(paths) or len({a['displayed_filename'] for a in attachments}) != len(paths):
                    raise ResearchError("upload_identity_unverified", "New attachment names are ambiguous")
                return attachments
            await asyncio.sleep(0.5)
        raise ResearchError("upload_not_ready", "Attachments did not become ready before the upload deadline")

    async def prepare_send(self, page, spec, prompt: str, paths: list[Path]) -> dict:
        self.prepared_attachments.pop(page, None)
        if len(prompt) >= 8000:
            raise ResearchError("composer_limit_exceeded", "Move long context into an assigned task brief attachment")
        stage, proof = "access", {}
        timings = {}
        stage_started = time.monotonic()
        def advance(value):
            nonlocal stage, stage_started
            timings[stage] = round(time.monotonic() - stage_started, 3)
            stage, stage_started = value, time.monotonic()
        before = Counter()
        try:
            await self.check_access(page)
            advance("attachment_inventory")
            await self.empty_attachment_inventory(page)
            advance("model")
            proof["model"] = await self.select_model(page, spec.model)
            advance("effort")
            proof["effort"] = await self.select_effort(page, spec.effort)
            advance("prompt")
            await (await self.composer(page)).fill(prompt)
            advance("web_search")
            await self.select_tool(page)
            advance("upload")
            await self.empty_attachment_inventory(page)
            attachments = await self.upload(page, paths)
            advance("final_configuration")
            selected = await self.confirm_configuration(page, spec.model, spec.effort)
            await self.check_access(page)
            if not await self.tool_selected(page):
                raise ResearchError("web_search_lost", "Web search changed before sending")
            await self.verify_attachment_inventory(page, attachments)
            advance("final_prompt")
            actual = await self.prompt_text(page)
            if actual.replace("\r\n", "\n").strip() != prompt.replace("\r\n", "\n").strip():
                raise ResearchError("composer_mismatch", "Composer text does not match the approved prompt")
            advance("send_ready")
            send = await required(page.locator(SEND), "send_unavailable", hit=True)
            if not await send.is_enabled():
                raise ResearchError("send_not_ready", "Send control is disabled")
            timings[stage] = round(time.monotonic() - stage_started, 3)
            self.prepared_attachments[page] = attachments
            # A rejected admission can close the prepared tab without clicking.
            # Release its proof immediately even if the browser retains the page object.
            page.once("close", lambda *_: self.prepared_attachments.pop(page, None))
            return {**selected, "tool": "web_search", "tool_evidence": "composer_chip",
                    "attachments": attachments, "prompt_sha256": digest(prompt.encode()), "stage_seconds": timings}
        except Exception as exc:
            # No prompt, file body, history, tokens or account data in diagnostics.
            timings[stage] = round(time.monotonic() - stage_started, 3)
            exc.diagnostic = {"stage": stage, "requested_model": spec.model,
                              "requested_effort": spec.effort, "confirmed": proof, "stage_seconds": timings}
            if stage in {"upload", "final_configuration", "final_prompt", "send_ready"} and paths:
                try:
                    exc.diagnostic["upload"] = await asyncio.wait_for(self.upload_diagnostic(page, paths, before), 5)
                except Exception as diagnostic_error:
                    exc.diagnostic["upload_diagnostic_error"] = type(diagnostic_error).__name__
            raise

    async def click_send(self, page):
        # No retry here: a timeout after this call can still mean that the message was sent.
        if page not in self.prepared_attachments:
            raise ResearchError("send_not_prepared", "The composer has no verified preparation for this Send")
        send = await required(page.locator(SEND), "send_unavailable")
        attachments = self.prepared_attachments.pop(page)
        await self.verify_attachment_inventory(page, attachments)
        await send.click(timeout=10000)

    async def observe(self, page, marker: str, *, binding: TurnBinding | None = None) -> dict:
        """Observe a marker-owned tail or recover its already proven logical containers."""
        if binding is not None:
            binding = TurnBinding.model_validate(binding)
        await self.check_access(page, strict_alerts=False)
        try:
            url = saved_url(page.url)
        except ResearchError:
            url = None
        if binding and url != binding.conversation_url:
            raise ResearchError("conversation_changed", "The saved conversation URL changed")
        snapshot = await page.evaluate(OBSERVE_DOM, {"marker": marker})
        turns = snapshot["turns"]
        result = Observation(url=url, binding=binding).model_dump()

        def observed():
            return Observation.model_validate(result).model_dump()

        def identity_index(identity):
            if identity is None:
                return None
            matches = [index for index, turn in enumerate(turns) if turn["identity"] == identity]
            if len(matches) > 1:
                raise ResearchError("duplicate_remote_turn", "A logical turn ID identifies multiple containers")
            if matches and turns[matches[0]]["identity_conflict"]:
                raise ResearchError("conversation_changed", "A logical turn has conflicting identity attributes")
            return matches[0] if matches else None

        hits = [index for index, turn in enumerate(turns) if turn["marker_count"]]
        if sum(turn["marker_count"] for turn in turns) > 1:
            raise ResearchError("duplicate_remote_turn", "More than one submitted message has this job marker")
        bound_user = identity_index(binding.user_turn_id) if binding else None
        bound_assistant = identity_index(binding.assistant_turn_id) if binding else None
        result["marker_seen"] = bool(hits)
        if hits:
            user_index = hits[0]
            result["ownership"] = "marker"
            if binding and binding.user_turn_id:
                current_id = turns[user_index]["identity"]
                if current_id and current_id != binding.user_turn_id:
                    raise ResearchError("conversation_changed", "The owned user turn was replaced")
                if bound_user is not None and bound_user != user_index:
                    raise ResearchError("conversation_changed", "The marker moved to a different logical turn")
        elif binding and binding.user_turn_id and binding.assistant_turn_id:
            if bound_user is None or not turns[bound_user]["visible"]:
                return observed()
            user_index = bound_user
        else:
            return observed()

        user = turns[user_index]
        if user["identity_conflict"]:
            raise ResearchError("conversation_changed", "The owned user turn has conflicting identity attributes")
        identity_index(user["identity"])
        if any("user" in turn["roles"] for turn in turns[user_index + 1:]):
            raise ResearchError("conversation_changed", "A later user turn changed the owned conversation")
        if bound_assistant is not None and bound_assistant <= user_index:
            raise ResearchError("conversation_changed", "The bound assistant turn moved before the owned user turn")
        if "assistant" in user["roles"]:
            if binding and binding.user_turn_id:
                raise ResearchError("conversation_changed", "The bound user container changed roles")
            result.update(projection_state="unresolved", projection_reason="mixed_turn_roles")
            return observed()
        if url and user["identity"]:
            result["binding"] = TurnBinding(
                conversation_url=url, user_turn_id=user["identity"],
                assistant_turn_id=binding.assistant_turn_id if binding else None,
            ).model_dump()
        responses = [index for index in range(user_index + 1, len(turns))
                     if turns[index]["response_present"]]
        result["response_present"] = bool(responses)
        if len(responses) > 1:
            if binding and binding.assistant_turn_id:
                raise ResearchError("conversation_changed", "Additional assistant turns changed the bound response")
            result.update(generating=bool(snapshot["generating"]), projection_state="unresolved",
                          projection_reason="multiple_assistant_boundaries")
            return observed()
        assistant_index = responses[0] if responses else None
        assistant = turns[assistant_index] if assistant_index is not None else None
        if assistant is None and bound_assistant is not None and turns[bound_assistant]["visible"]:
            assistant_index, assistant = bound_assistant, turns[bound_assistant]
            result["response_present"] = True
        if binding and binding.assistant_turn_id:
            if assistant and assistant["identity"] and assistant["identity"] != binding.assistant_turn_id:
                raise ResearchError("conversation_changed", "The bound assistant turn was replaced")
            if bound_assistant is not None and assistant_index is not None and bound_assistant != assistant_index:
                raise ResearchError("conversation_changed", "The response moved to a different logical turn")
            if bound_assistant is not None and "user" in turns[bound_assistant]["roles"]:
                raise ResearchError("conversation_changed", "The bound assistant container changed roles")
        extra_logical_turns = [index for index in range(user_index + 1, len(turns))
                               if turns[index]["identity"] and index != assistant_index]
        if extra_logical_turns:
            if assistant is not None or (binding and binding.assistant_turn_id):
                raise ResearchError("conversation_changed", "Additional logical turns changed the owned conversation tail")
            pending = turns[extra_logical_turns[0]]
            if len(extra_logical_turns) == 1 and not pending["roles"] and not pending["response_present"] and not pending["identity_conflict"]:
                # A new turn can spend its thinking phase without an assistant
                # role or answer. This is waiting evidence, not lost projection;
                # its logical ID is still insufficient to own a Stop action.
                result.update(projection_state="awaiting", projection_reason="awaiting_assistant",
                              generating=bool(snapshot["generating"]), response_present=False)
                return observed()
            result.update(projection_state="unresolved", projection_reason="unproven_logical_tail")
            return observed()
        if not hits:
            if bound_assistant is None or not turns[bound_assistant]["visible"]:
                return observed()
            if assistant_index is not None and assistant_index != bound_assistant:
                raise ResearchError("conversation_changed", "The bound assistant response was replaced")
            # An empty but still rendered logical assistant container remains owned.
            assistant_index, assistant = bound_assistant, turns[bound_assistant]
            result.update(ownership="bound_ids", response_present=True)
        elif binding and binding.assistant_turn_id and (
            bound_assistant is None or (assistant and not assistant["identity"])
        ):
            result.update(projection_state="unresolved", projection_reason="bound_assistant_identity_missing")
            return observed()

        user_id = user["identity"] or (binding.user_turn_id if binding else None)
        assistant_id = assistant["identity"] if assistant else None
        if assistant and assistant["identity_conflict"]:
            raise ResearchError("conversation_changed", "The owned assistant turn has conflicting identity attributes")
        for identity in (user_id, assistant_id):
            identity_index(identity)
        assistant_id = assistant_id or (binding.assistant_turn_id if binding else None)
        if url and (user_id or assistant_id):
            result["binding"] = TurnBinding(conversation_url=url, user_turn_id=user_id,
                                             assistant_turn_id=assistant_id).model_dump()
        result["generating"] = bool(snapshot["generating"])
        if not assistant:
            return observed()
        result["terminal_reason"] = assistant["terminal_reason"]
        if assistant["projection_unproven"]:
            result.update(projection_state="unresolved", projection_reason="answer_roots_unproven")
            return observed()
        blocks = [html_to_markdown(fragment["html"]) or fragment["text"]
                  for fragment in assistant["fragments"]]
        blocks = [block for block in blocks if block.strip()]
        text = "\n\n".join(fragment["text"] for fragment in assistant["fragments"] if fragment["text"])
        markdown = "\n\n".join(blocks)
        result.update(text=text, markdown=markdown, blocks=blocks, links=assistant["links"],
                      unresolved_citations=assistant["unresolved_citations"],
                      projection_state="snapshot" if markdown or text else "empty",
                      turn_ended=bool(markdown or text) and bool(assistant["final_control"])
                      and not result["generating"] and not result["terminal_reason"])
        result["web_search_evidence"] = [
            {"type": "visible_response_link", **link}
            for link in assistant["links"]
            if urlparse(link["url"]).scheme in {"http", "https"}
            and urlparse(link["url"]).hostname not in {"chatgpt.com", "www.chatgpt.com"}
        ]
        return observed()

    async def cancel(self, page, marker: str, *, binding: TurnBinding | None = None,
                     deadline_at: str | None = None) -> dict:
        budget = ((datetime.fromisoformat(deadline_at) - datetime.now(timezone.utc)).total_seconds()
                  if deadline_at else None)
        if budget is not None and budget <= 0:
            return {"confirmed": False, "reason": "generation_deadline_exceeded"}

        async def stop_owned_tail():
            current_binding = binding

            async def current():
                nonlocal current_binding
                state = Observation.model_validate(await self.observe(page, marker, binding=current_binding))
                current_binding = state.binding or current_binding
                return state

            def owned(state):
                return state.ownership in {"marker", "bound_ids"} and state.projection_state not in {"unresolved", "awaiting"}

            def ended(state):
                return (state.turn_ended or state.terminal_reason is not None) and not state.generating

            before = await current()
            if not owned(before):
                return {"confirmed": False, "reason": "owned_turn_not_found"}
            if ended(before):
                return {"confirmed": True, "reason": "turn_already_ended"}
            controls = await visible_controls(page.locator(STOP))
            if len(controls) != 1:
                return {"confirmed": False, "reason": "stop_control_not_observed"}
            # Recheck after locating Stop: the saved binding also applies to this click.
            before = await current()
            if not owned(before):
                return {"confirmed": False, "reason": "owned_turn_not_found"}
            if ended(before):
                return {"confirmed": True, "reason": "turn_already_ended"}
            if not before.generating:
                return {"confirmed": False, "reason": "stop_control_not_observed"}
            await controls[0].click(timeout=8000)
            until = time.monotonic() + 10
            while time.monotonic() < until:
                state = await current()
                if not owned(state):
                    return {"confirmed": False, "reason": "owned_turn_not_found"}
                if ended(state):
                    return {"confirmed": True, "reason": "stop_confirmed"}
                await asyncio.sleep(min(1, max(0, until - time.monotonic())))
            return {"confirmed": False, "reason": "stop_not_confirmed"}

        deadline = asyncio.timeout(budget)
        try:
            # This bounds observation, the click, and every confirmation poll by
            # the original generation deadline; cancellation never renews it.
            async with deadline:
                return await stop_owned_tail()
        except TimeoutError:
            if not deadline.expired():
                raise
            return {"confirmed": False, "reason": "generation_deadline_exceeded"}

    async def capabilities(self, page) -> dict:
        await self.check_access(page)
        selected = await self.read_configuration(page)
        scope = await self.model_menu(page)
        models = [{"label": normalized(await node.inner_text()), "selected": await self.checked(node)}
                  for node in await visible_controls(scope.locator(
                      '[role="menuitemradio"], [role="option"], [role="radio"]'))]
        await page.keyboard.press("Escape")
        return {"login": "observed", **selected, "models": models,
                "scope": "UI availability only; no prompt sent"}
