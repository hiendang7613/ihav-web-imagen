# Adapted from the user-local AutoCodex chatgpt_web.py. See references/provenance.md.
from __future__ import annotations

import re
from html.parser import HTMLParser

class _MarkdownExtractor(HTMLParser):
    """Minimal HTML->Markdown converter focused on preserving tables.

    Rendered chat responses lose their "|" table pipes when read via inner_text.
    This walks the message HTML and emits Markdown: real pipe tables, plus
    headings/lists/paragraphs. Inline tags collapse to their text.
    """

    _BLOCK = {"p", "div", "section", "article"}
    _HEADING = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
    _SKIP_CONTENT = {"script", "style", "noscript", "template"}
    _COPY_LABELS = {"copy", "copy code", "copy response"}
    _COPY_TEST_IDS = {"copy-turn-action-button", "copy-code-button"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.table: list[list[str]] | None = None
        self.row: list[str] | None = None
        self.cell: list[str] | None = None
        self.cell_code_indices: list[int] = []
        self.pre: list[str] | None = None
        self.pre_language = ""
        self.code_part_indices: set[int] = set()
        self.list_stack: list[list] = []
        # KaTeX state: capture the LaTeX source from <annotation> and suppress the
        # rendered glyphs, which otherwise jumble into the text (rendered glyphs +
        # raw LaTeX) when a math span is read as plain text.
        self.in_katex = False
        self.katex_open = 0
        self.katex_display = False
        self.next_display = False
        self.pending_display_tag: str | None = None
        self.cap_latex = False
        self.latex: list[str] = []
        self.link_stack: list[str] = []
        self.inline_code: list[str] | None = None
        self.skip_content: list[str] = []
        self.li_depth = 0
        self.blockquote_depth = 0
        self.details_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001
        if self.skip_content:
            if tag in self._SKIP_CONTENT or tag == self.skip_content[-1]:
                self.skip_content.append(tag)
            return
        attrs_dict = dict(attrs)
        is_copy_control = tag == "button" and (
            attrs_dict.get("data-testid") in self._COPY_TEST_IDS
            or any((attrs_dict.get(name) or "").strip().casefold() in self._COPY_LABELS
                   for name in ("aria-label", "title"))
        )
        if tag in self._SKIP_CONTENT or is_copy_control:
            self.skip_content.append(tag)
            return
        classes = (attrs_dict.get("class") or "").split()
        if self.pre is not None:
            if tag == "br":
                self.pre.append("\n")
            elif tag == "code" and not self.pre_language:
                for class_name in classes:
                    match = re.fullmatch(r"language-([A-Za-z0-9][A-Za-z0-9_+.#-]*)", class_name)
                    if match:
                        self.pre_language = match.group(1)
                        break
            return
        if self.in_katex:
            self.katex_open += 1
            if tag == "annotation" and dict(attrs).get("encoding") == "application/x-tex":
                self.cap_latex = True
                self.latex = []
            return
        if "katex-display" in classes:
            self.next_display = True
            self.pending_display_tag = tag
        if "katex" in classes:
            # Enter the math span: emit only the LaTeX source, drop everything else.
            self.in_katex = True
            self.katex_open = 1
            self.katex_display = self.next_display
            self.next_display = False
            self.pending_display_tag = None
            self.cap_latex = False
            self.latex = []
            return
        if tag == "pre" and self.table is None:
            self.pre = []
            self.pre_language = ""
            return
        if tag == "code":
            self.inline_code = []
            return
        if self.inline_code is not None:
            return
        if tag == "input" and (attrs_dict.get("type") or "").lower() == "checkbox":
            if self.li_depth > 0 or self.cell is not None:
                self._append_text("[x] " if "checked" in attrs_dict else "[ ] ")
            return
        if self.table is None and tag == "details":
            self.details_depth += 1
            open_attr = " open" if "open" in attrs_dict else ""
            self._append_raw(f"\n\n<details{open_attr}>\n\n")
            return
        if self.table is None and self.details_depth > 0 and tag == "summary":
            self._append_raw("<summary>")
            return
        if tag == "img":
            alt = (attrs_dict.get("alt") or attrs_dict.get("title") or "image").strip() or "image"
            src = (attrs_dict.get("src") or "").strip()
            if self.link_stack:
                self._append_text(alt)
            elif src:
                self._append_raw(f"![{_escape_link_label(alt)}]({_escape_link_destination(src)})")
            else:
                self._append_text(alt)
            return
        if tag in ("strong", "b"):
            self._append_raw("**")
            return
        if tag in ("em", "i"):
            self._append_raw("*")
            return
        if tag == "kbd":
            self._append_raw("<kbd>")
            return
        if tag == "sup":
            self._append_raw("<sup>")
            return
        if tag == "sub":
            self._append_raw("<sub>")
            return
        if tag in ("del", "s", "strike"):
            self._append_raw("~~")
            return
        if tag == "hr" and self.table is None:
            self.parts.append("\n\n---\n\n")
            return
        if tag == "dt" and self.table is None:
            self._append_raw("\n\n- **")
            return
        if tag == "dd" and self.table is None:
            self._append_raw(": ")
            return
        if tag == "figcaption" and self.table is None:
            self._append_raw("\n\n*")
            return
        if tag == "table":
            self.table = []
        elif tag == "tr" and self.table is not None:
            self._finish_table_cell()
            self._finish_table_row()
            self.row = []
        elif tag in ("td", "th") and self.row is not None:
            self._finish_table_cell()
            self.cell = []
        elif tag == "a":
            href = (attrs_dict.get("href") or "").strip()
            if href:
                self._append_text("[")
                self.link_stack.append(href)
            else:
                self.link_stack.append("")
        elif self.table is not None:
            if tag == "br" and self.cell is not None:
                self.cell.append("<br>")
            return
        elif tag == "br":
            self.parts.append("\n")
        elif tag == "blockquote":
            self.blockquote_depth += 1
            self.parts.append("\n\n")
        elif tag in self._BLOCK:
            self.parts.append("\n\n")
        elif tag in ("ul", "ol"):
            start = _parse_ordered_list_start(attrs_dict.get("start")) if tag == "ol" else 0
            self.list_stack.append([tag, start - 1 if start > 1 else 0])
            self.parts.append("\n")
        elif tag == "li":
            self.li_depth += 1
            quote_prefix = "> " * self.blockquote_depth
            indent = "  " * max(0, len(self.list_stack) - 1)
            self.parts.append("\n" + quote_prefix + indent)
            if self.list_stack and self.list_stack[-1][0] == "ol":
                self.list_stack[-1][1] += 1
                self.parts.append(f"{self.list_stack[-1][1]}. ")
            else:
                self.parts.append("- ")
        elif tag in self._HEADING:
            self.parts.append("\n\n" + "#" * self._HEADING[tag] + " ")

    def handle_endtag(self, tag: str) -> None:
        if self.skip_content:
            if tag == self.skip_content[-1]:
                self.skip_content.pop()
            return
        if self.pre is not None:
            if tag == "pre":
                code = "".join(self.pre)
                self.pre = None
                if code:
                    longest = max((len(m.group(0)) for m in re.finditer(r"`+", code)), default=0)
                    fence = "`" * max(3, longest + 1)
                    closing_newline = "" if code.endswith("\n") else "\n"
                    self.parts.append("\n\n")
                    self.code_part_indices.add(len(self.parts))
                    self.parts.append(f"{fence}{self.pre_language}\n{code}{closing_newline}{fence}")
                    self.parts.append("\n\n")
                self.pre_language = ""
            return
        if self.in_katex:
            if tag == "annotation":
                self.cap_latex = False
            self.katex_open -= 1
            if self.katex_open <= 0:
                self.in_katex = False
                self._emit_katex()
            return
        if tag == "a" and self.link_stack:
            href = self.link_stack.pop()
            if href:
                self._append_text(f"]({_escape_link_destination(href)})")
            return
        if tag == "code" and self.inline_code is not None:
            code = "".join(self.inline_code)
            self.inline_code = None
            self._append_text(_render_inline_code(code))
            if self.cell is not None:
                self.cell_code_indices.append(len(self.cell) - 1)
            return
        if self.inline_code is not None:
            return
        if tag == "summary" and self.details_depth > 0:
            self._append_raw("</summary>\n\n")
            return
        if tag == "details" and self.details_depth > 0:
            self.details_depth -= 1
            self._append_raw("\n</details>\n\n")
            return
        if tag in ("strong", "b"):
            self._append_raw("**")
            return
        if tag in ("em", "i"):
            self._append_raw("*")
            return
        if tag == "kbd":
            self._append_raw("</kbd>")
            return
        if tag == "sup":
            self._append_raw("</sup>")
            return
        if tag == "sub":
            self._append_raw("</sub>")
            return
        if tag in ("del", "s", "strike"):
            self._append_raw("~~")
            return
        if tag == "dt" and self.table is None:
            self._append_raw("**")
            return
        if tag == "dd" and self.table is None:
            self._append_raw("\n")
            return
        if tag == "figcaption" and self.table is None:
            self._append_raw("*\n\n")
            return
        if tag == "table" and self.table is not None:
            self._finish_table_cell()
            self._finish_table_row()
            self.parts.append("\n\n" + self._render_table(self.table) + "\n\n")
            self.table = None
        elif tag == "tr" and self.row is not None:
            self._finish_table_cell()
            self._finish_table_row()
        elif tag in ("td", "th") and self.cell is not None:
            self._finish_table_cell()
        elif self.table is not None:
            return
        elif tag == "blockquote" and self.blockquote_depth > 0:
            self.blockquote_depth -= 1
            self.parts.append("\n")
        elif tag in self._BLOCK or tag in self._HEADING:
            self.parts.append("\n")
        elif tag == "li" and self.li_depth > 0:
            self.li_depth -= 1
        elif tag in ("ul", "ol") and self.list_stack:
            self.list_stack.pop()
            self.parts.append("\n")
        if self.next_display and tag == self.pending_display_tag:
            self.next_display = False
            self.pending_display_tag = None

    def handle_data(self, data: str) -> None:
        if self.skip_content:
            return
        if self.in_katex:
            if self.cap_latex:
                self.latex.append(data)
            return
        if self.pre is not None:
            self.pre.append(data)
            return
        if self.inline_code is not None:
            self.inline_code.append(data)
            return
        self._append_text(data)

    def _emit_katex(self) -> None:
        latex = "".join(self.latex).strip()
        self.latex = []
        if not latex:
            return
        if self.katex_display:
            rendered = "$$\n" + latex + "\n$$"
            if self.cell is not None:
                self.cell.append(rendered)
            else:
                self.parts.append("\n\n" + rendered + "\n\n")
        else:
            rendered = "$" + latex + "$"
            if self.cell is not None:
                self.cell.append(rendered)
            else:
                self.parts.append(rendered)

    def _append_text(self, text: str) -> None:
        if self.link_stack:
            text = (
                text.replace("\\", "\\\\")
                .replace("[", r"\[")
                .replace("]", r"\]")
                .replace("*", r"\*")
                .replace("`", r"\`")
            )
        if self.cell is not None:
            if self.cell and self.cell[-1].endswith(("[x] ", "[ ] ")) and text[:1].isspace():
                text = text.lstrip()
            self.cell.append(text)
        elif self.table is None:
            if self.parts and self.parts[-1].endswith(("[x] ", "[ ] ")) and text[:1].isspace():
                text = text.lstrip()
            if self.blockquote_depth > 0 and text.strip():
                text = self._prefix_blockquote_text(text)
            self.parts.append(text)

    def _append_raw(self, text: str) -> None:
        if self.cell is not None:
            self.cell.append(text)
        elif self.table is None:
            self.parts.append(text)

    def _finish_table_cell(self) -> None:
        if self.cell is None:
            return
        if self.row is not None:
            # Preserve code-span whitespace while keeping the surrounding prose
            # on one table row. Fences protect code edges from the final strip.
            rendered: list[str] = []
            prose_start = 0
            for index in self.cell_code_indices:
                rendered.append(re.sub(r"\s+", " ", "".join(self.cell[prose_start:index])))
                rendered.append(self.cell[index])
                prose_start = index + 1
            rendered.append(re.sub(r"\s+", " ", "".join(self.cell[prose_start:])))
            self.row.append("".join(rendered).strip())
        self.cell = None
        self.cell_code_indices = []

    def _finish_table_row(self) -> None:
        if self.row is None:
            return
        if self.table is not None:
            self.table.append(self.row)
        self.row = None

    def _prefix_blockquote_text(self, text: str) -> str:
        prefix = "> " * self.blockquote_depth
        at_line_start = not self.parts or self.parts[-1].endswith("\n")
        lines = text.splitlines(keepends=True)
        out: list[str] = []
        for line in lines:
            if at_line_start and line.strip():
                out.append(prefix)
            out.append(line)
            at_line_start = line.endswith("\n")
        return "".join(out)

    @staticmethod
    def _render_table(rows: list[list[str]]) -> str:
        rows = [r for r in rows if r]
        if not rows:
            return ""
        ncol = max(len(r) for r in rows)
        rows = [r + [""] * (ncol - len(r)) for r in rows]
        rows = [[_escape_table_cell_pipes(cell) for cell in r] for r in rows]
        lines = [
            "| " + " | ".join(rows[0]) + " |",
            "| " + " | ".join(["---"] * ncol) + " |",
        ]
        for r in rows[1:]:
            lines.append("| " + " | ".join(r) + " |")
        return "\n".join(lines)

    def get_markdown(self) -> str:
        # Normalize only prose. Code parts already include their fences, which
        # protect meaningful leading/trailing whitespace from the final strip.
        rendered: list[str] = []
        prose_start = 0
        for index in sorted(self.code_part_indices):
            rendered.append(_normalize_prose("".join(self.parts[prose_start:index])))
            rendered.append(self.parts[index])
            prose_start = index + 1
        rendered.append(_normalize_prose("".join(self.parts[prose_start:])))
        return "".join(rendered).strip()


def _normalize_prose(text: str) -> str:
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text)


def _escape_table_cell_pipes(cell: str) -> str:
    """Escape Markdown table pipes while preserving literal preceding slashes.

    A table parser uses an odd backslash run before ``|`` as the escape marker.
    To preserve ``k`` literal backslashes before a literal pipe, emit
    ``2k + 1`` backslashes before that pipe.
    """
    out: list[str] = []
    for i, ch in enumerate(cell):
        if ch == "|":
            slash_count = 0
            j = i - 1
            while j >= 0 and cell[j] == "\\":
                slash_count += 1
                j -= 1
            out.append("\\" * (slash_count + 1))
        out.append(ch)
    return "".join(out)


def _escape_link_destination(href: str) -> str:
    """Escape Markdown link destination chars that commonly break URLs."""
    return href.replace("\\", "\\\\").replace("(", r"\(").replace(")", r"\)")


def _escape_link_label(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace("[", r"\[")
        .replace("]", r"\]")
        .replace("*", r"\*")
        .replace("`", r"\`")
    )


def _parse_ordered_list_start(value: str | None) -> int:
    try:
        parsed = int(str(value or "").strip())
    except ValueError:
        return 1
    return parsed if parsed > 0 else 1


def _render_inline_code(text: str) -> str:
    """Render text as a Markdown inline code span, even when it contains ticks."""
    # Code spans normalize line endings to spaces. Do this before deciding on
    # padding, and keep literal spaces alongside those normalized line endings.
    text = re.sub(r"\r\n?|\n", " ", text)
    longest = max((len(match.group(0)) for match in re.finditer(r"`+", text)), default=0)
    fence = "`" * (longest + 1)
    # Markdown removes one space from each edge only when both edges are spaces
    # and the content is not entirely spaces. Supply that padding explicitly.
    needs_space_padding = text.startswith(" ") and text.endswith(" ") and bool(text.strip(" "))
    if text.startswith("`") or text.endswith("`") or needs_space_padding:
        return f"{fence} {text} {fence}"
    return f"{fence}{text}{fence}"


def html_to_markdown(html: str) -> str:
    """Convert response HTML to Markdown, preserving tables. '' on empty/failure."""
    if not html:
        return ""
    parser = _MarkdownExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # noqa: BLE001
        return ""
    return parser.get_markdown()
