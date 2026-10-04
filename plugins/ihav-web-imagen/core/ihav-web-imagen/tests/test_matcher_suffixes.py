# Regression cases for prompt text that ends like a UI collapse control.
"""Prompt characters survive UI collapse/expand controls (local file fixtures)."""

import unittest

from test_writer_fixes import BrowserCase, collapsed, expanded, turn_html


class PromptSuffixTests(BrowserCase):
    async def assert_matches(self, prompt, render):
        await self.html(turn_html(render(prompt)))
        await self.paint_result()
        self.assertEqual((await self.view(prompt))['matches'], 1)

    async def test_expanded_prompt_ending_in_ellipsis_is_preserved(self):
        await self.assert_matches('Continue the story…', expanded)

    async def test_literal_toggle_words_at_the_prompt_end_are_preserved(self):
        for prompt in ('The label is Show more', 'The label is Show less', 'Keep punctuation… Show more'):
            for render in (collapsed, expanded):
                with self.subTest(prompt=prompt, render=render.__name__):
                    await self.assert_matches(prompt, render)

    async def test_marker_is_removed_only_when_it_is_a_separate_ui_span(self):
        prompt = 'Continue the story…'
        await self.html(turn_html(f'<div>{prompt}</div><button>Show more</button>'))
        await self.paint_result()
        self.assertEqual((await self.view(prompt))['matches'], 1)
        self.assertEqual((await self.view('Continue the story'))['matches'], 0)


if __name__ == '__main__':
    unittest.main()
