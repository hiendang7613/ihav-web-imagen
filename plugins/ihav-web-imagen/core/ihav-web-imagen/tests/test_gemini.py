"""Gemini adapter against a scripted local page in the real browser runtime (no network, no Gemini request)."""
import asyncio
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import gemini  # noqa: E402

PAGE = r'''<!doctype html><html><body>
<div class="cdk-overlay-container"></div>
<div class="text-input-field"><div class="ql-editor" role="textbox" contenteditable="true" aria-label="Enter a prompt"></div>
<button aria-label="Uploads and tools" id="tools">+</button><span id="chips"></span><span id="send-slot"></span></div>
<main id="chat"></main>
<script>
window.sendCount = 0;
const ed = document.querySelector('.ql-editor');
document.getElementById('tools').onclick = () => {
  const o = document.querySelector('.cdk-overlay-container');
  o.innerHTML = '<button role="menuitemcheckbox" aria-checked="false">Create image</button>';
  o.firstChild.onclick = () => { o.innerHTML=''; document.getElementById('chips').innerHTML = '<button aria-label="Deselect Image">Image</button>'; };
};
new MutationObserver(() => {
  const slot = document.getElementById('send-slot');
  if (ed.innerText.trim() && !slot.firstChild) {
    slot.innerHTML = '<button aria-label="Send message">&gt;</button>';
    slot.firstChild.onclick = () => {
      window.sendCount++;
      const r = document.createElement('model-response');
      document.getElementById('chat').appendChild(r);
      setTimeout(() => { const c=document.createElement('canvas'); c.width=c.height=512; const x=c.getContext('2d');
        x.fillStyle='#c33'; x.fillRect(0,0,512,512); const img=new Image(); img.src=c.toDataURL(); r.appendChild(img); }, 300);
    };
  }
}).observe(ed, {childList:true, subtree:true, characterData:true});
</script></body></html>'''


class GeminiFixtureTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        try:
            from chatgpt_web.core import DEFAULT_STATE, load_config
            from chatgpt_web.browser import cloak_binary
            self.config = load_config(DEFAULT_STATE)
            cloak_binary(self.config)
        except Exception as exc:
            self.skipTest(f'browser runtime not set up: {exc}')
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        (root / 'chat.html').write_text(PAGE)
        self.url = (root / 'chat.html').as_uri()
        from chatgpt_web.browser import Browser
        self.browser = Browser(root, self.config)
        await self.browser.start(headless=True)
        self.page = await self.browser.context.new_page()
        await self.page.goto(self.url)
        self.receipt = gemini.receipt_for(root, 'g-run')
        self.receipt.create('g-run', 'a red fox', download_stem=str(root / 'g-run'))

    async def asyncTearDown(self):
        if hasattr(self, 'browser'):
            await self.browser.close()
            self.temp.cleanup()

    async def test_one_send_in_image_mode_then_the_image_is_observed(self):
        await gemini.submit(self.page, self.receipt)
        self.assertEqual(self.receipt.data['send_state'], 'intent')
        self.assertEqual(self.receipt.data['send_clicks'], 1)
        self.assertTrue(await gemini.image_mode(self.page))
        done = await gemini.observe(self.page, self.receipt, wait_seconds=10)
        self.assertTrue(done)
        self.assertEqual(self.receipt.data['send_state'], 'confirmed')
        self.assertEqual(self.receipt.data['image_dimensions'], [[512, 512]])
        self.assertEqual(await self.page.evaluate('window.sendCount'), 1)

    async def test_a_second_submit_is_refused_and_does_not_click(self):
        await gemini.submit(self.page, self.receipt)
        with self.assertRaises(gemini.ResearchError) as error:
            await gemini.submit(self.page, self.receipt)
        self.assertEqual(error.exception.code, 'resend_forbidden')
        self.assertEqual(await self.page.evaluate('window.sendCount'), 1)

    async def test_a_leftover_draft_stops_before_the_intent(self):
        await self.page.locator(gemini.EDITOR).click()
        await self.page.keyboard.insert_text('somebody else')
        with self.assertRaises(gemini.ResearchError) as error:
            await gemini.submit(self.page, self.receipt)
        self.assertEqual(error.exception.code, 'unexpected_draft')
        self.assertEqual((self.receipt.data['send_state'], self.receipt.data['send_clicks']), ('not_sent', 0))
        self.assertEqual(await self.page.evaluate('window.sendCount'), 0)


class UrlTests(unittest.TestCase):
    def test_only_a_gemini_chat_url_is_kept(self):
        self.assertEqual(gemini.chat_url('https://gemini.google.com/app/1a2b3c4d5e6f?hl=vi'), 'https://gemini.google.com/app/1a2b3c4d5e6f')
        self.assertIsNone(gemini.chat_url('https://gemini.google.com/app'))
        self.assertIsNone(gemini.chat_url('https://evil.example/app/1a2b3c4d5e6f'))


if __name__ == '__main__':
    unittest.main()
