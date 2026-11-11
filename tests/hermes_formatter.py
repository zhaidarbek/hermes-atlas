"""Hermes v0.23.0's Telegram formatter, copied verbatim (MIT) so tests run what Hermes runs.

Each block below is one definition, byte for byte, from the pinned Hermes source:
  plugins/platforms/telegram/adapter.py   _MDV2_ESCAPE_RE, _escape_mdv2, TelegramAdapter.format_message,
                                          TelegramAdapter._escape_bare_bracket,
                                          TelegramAdapter._needs_rich_rendering
  gateway/platforms/helpers.py            TABLE_SEPARATOR_RE, is_table_row, convert_table_to_bullets
  tools/send_message_senders.py           _telegram_format (its HTML test; parse modes as strings here)
_render_table_block is not copied: a table in a message Atlas styled fails the test that meets it.
test_telegram.py checks each copy against the source when ATLAS_HERMES_SRC names an extracted
Hermes v0.23.0 tree.
"""
import re

_TELEGRAM_FORMAT_HTML = re.compile(r'<[a-zA-Z/][^>]*>')


def _render_table_block(table_block):
    raise AssertionError("format_message read a table in a message Atlas styled")


TABLE_SEPARATOR_RE = re.compile(r'^\s*\|?\s*:?-+:?\s*(?:\|\s*:?-+:?\s*){1,}\|?\s*$')

def is_table_row(line: str) -> bool:
    """Return True if *line* could plausibly be a table data row."""
    return '|' in line.strip()


def convert_table_to_bullets(text: str) -> str:
    """Rewrite GFM pipe tables into bold-heading + bullet groups; fenced code is left alone."""
    if '|' not in text or '-' not in text:
        return text
    lines = text.split('\n')
    out: list[str] = []
    in_fence = False
    i = 0
    while i < len(lines):
        line = lines[i]
        is_fence_line = line.lstrip().startswith('```')
        in_fence ^= is_fence_line
        if not (in_fence or is_fence_line) and '|' in line and i + 1 < len(lines) \
                and TABLE_SEPARATOR_RE.match(lines[i + 1]):
            j = i + 2
            while j < len(lines) and is_table_row(lines[j]):
                j += 1
            out.append(_render_table_block(lines[i:j]))
            i = j
        else:
            out.append(line)
            i += 1
    return '\n'.join(out)


_MDV2_ESCAPE_RE = re.compile(r'([_*\[\]()~`>#\+\-=|{}.!\\])')


def _escape_mdv2(text: str) -> str:
    """Escape Telegram MarkdownV2 special characters with a preceding backslash."""
    return _MDV2_ESCAPE_RE.sub(r'\\\1', text)


_wrap_markdown_tables = convert_table_to_bullets
_TABLE_SEPARATOR_RE = TABLE_SEPARATOR_RE


class TelegramAdapter:
    def format_message(self, content: str) -> str:
        """Convert standard markdown to Telegram MarkdownV2: code is stashed behind placeholders first (never
        modified), markdown constructs become MarkdownV2 syntax, everything else is escaped."""
        if not content:
            return content
        placeholders: dict = {}
        counter = [0]

        def _ph(value: str) -> str:
            """Stash *value* behind a placeholder token that survives escaping."""
            key = f"\x00PH{counter[0]}\x00"
            counter[0] += 1
            placeholders[key] = value
            return key

        def _ph_wrap(open_: str, close: str):
            return lambda m: _ph(f"{open_}{_escape_mdv2(m.group(1))}{close}")

        # 0) Rewrite GFM-style pipe tables into Telegram-friendly row groups
        #    before the normal MarkdownV2 conversions run.
        text = _wrap_markdown_tables(content)

        # 1) Protect fenced code blocks (``` ... ```)
        #    Per MarkdownV2 spec, \ and ` inside pre/code must be escaped.
        #    A fence still opens on its own line — the opening ``` must be the
        #    first triple-backtick run on that line and must end it — but the
        #    line may carry arbitrary leading whitespace (list/blockquote-
        #    nested code indents fences by 4+ spaces) or lead-in prose
        #    ("Here is the code: ```"), both of which the line-start-only
        #    anchor silently downgraded from <pre> to escaped literal text.
        #    Requiring the rest of the opening line to be backtick-free is
        #    what keeps *inline* triple backticks (e.g. "the syntax is
        #    ```x``` inline") out of the match: a closing run can never sit
        #    on the same line as the opener, and the tempered prefix cannot
        #    skip past an earlier run to a later one.  The closing fence must
        #    sit on its own line (any indent), with optional trailing
        #    whitespace and an optional ``\r`` so CRLF-terminated fences
        #    (Windows-authored content) match too.
        def _protect_fenced(m):
            prefix = m.group(1)   # lead-in text / indent before the opening fence
            opening = m.group(2)  # opening ``` (with optional language) and newline
            body = m.group(3)     # code body (may be empty)
            closing = m.group(4)   # closing fence (with its indent)
            body = body.replace('\\', '\\\\').replace('`', '\\`')
            return prefix + _ph(opening + body + closing)

        text = re.sub(
            r'(?m)^((?:(?!```)[^\n])*)(```[^`\n]*\n)([\s\S]*?)(^[ \t]*```)[ \t]*\r?$',
            _protect_fenced,
            text,
        )

        # 2) Protect inline code (`...`)
        #    Escape \ inside inline code per MarkdownV2 spec.
        text = re.sub(
            r'(`[^`]+`)',
            lambda m: _ph(m.group(0).replace('\\', '\\\\')),
            text,
        )

        # 3) Convert markdown links – escape the display text; inside the URL
        #    only ')' and '\' need escaping per the MarkdownV2 spec.
        def _convert_link(m):
            url = m.group(2).replace('\\', '\\\\').replace(')', '\\)')
            return _ph(f'[{_escape_mdv2(m.group(1))}]({url})')

        text = re.sub(r'\[([^\]]+)\]\(([^()]*(?:\([^()]*\)[^()]*)*)\)', _convert_link, text)
        # 4) Headers (## Title) → bold *Title*, stripping redundant ** inside the header
        def _convert_header(m):
            inner = re.sub(r'\*\*(.+?)\*\*', r'\1', m.group(1).strip())
            return _ph(f'*{_escape_mdv2(inner)}*')

        text = re.sub(r'^#{1,6}\s+(.+)$', _convert_header, text, flags=re.MULTILINE)
        # 5) Bold **text** → *text*; 6) Italic *text* → _text_ ([^*\n]+ keeps matches on one line, or *
        # bullet lists corrupt); 7) Strikethrough ~~text~~ → ~text~; 8) Spoiler ||text|| kept as-is.
        text = re.sub(r'\*\*(.+?)\*\*', _ph_wrap('*', '*'), text)
        text = re.sub(r'\*([^*\n]+)\*', _ph_wrap('_', '_'), text)
        text = re.sub(r'~~(.+?)~~', _ph_wrap('~', '~'), text)
        text = re.sub(r'\|\|(.+?)\|\|', _ph_wrap('||', '||'), text)
        # 9) Blockquotes: protect leading > from escaping; expandable quotes (**> starts, trailing || ends).
        def _convert_blockquote(m):
            prefix, content = m.group(1), m.group(2)  # prefix: >, >>, >>>, **>, **>> …
            if prefix.startswith('**') and content.endswith('||'):
                return _ph(f'{prefix} {_escape_mdv2(content[:-2])}||')
            return _ph(f'{prefix} {_escape_mdv2(content)}')

        text = re.sub(r'^((?:\*\*)?>{1,3}) (.+)$', _convert_blockquote, text, flags=re.MULTILINE)
        # 10) Escape remaining special characters in plain text
        text = _escape_mdv2(text)
        # 11) Restore placeholders in reverse insertion order so nested placeholders resolve.
        for key in reversed(list(placeholders.keys())):
            text = text.replace(key, placeholders[key])
        # 12) Safety net: escape bare ( ) { } that slipped through, but never inside ``` or ` spans.
        _safe_parts = []
        for _idx, _seg in enumerate(re.split(r'(```[\s\S]*?```|`[^`]+`)', text)):
            if _idx % 2 == 1:
                _safe_parts.append(_seg)  # inside code — untouched
            else:
                _safe_parts.append(re.sub(r'[(){}]', lambda m, _seg=_seg: TelegramAdapter._escape_bare_bracket(m, _seg), _seg))
        return ''.join(_safe_parts)

    @staticmethod
    def _escape_bare_bracket(m, seg: str) -> str:
        """Escape a bare ( ) { } unless it is already escaped or delimits a ``[text](url)`` link."""
        s = m.start()
        ch = m.group(0)
        if s > 0 and seg[s - 1] == '\\':  # already escaped
            return ch
        if ch == '(' and s > 0 and seg[s - 1] == ']':  # opens a link [text](url)
            return ch
        if ch == ')':  # closes a link URL? walk back matching depth
            before = seg[:s]
            if '](http' in before or '](' in before:
                depth = 0
                for j in range(s - 1, max(s - 2000, -1), -1):
                    if seg[j] == '(':
                        depth -= 1
                        if depth < 0:
                            if j > 0 and seg[j - 1] == ']':
                                return ch
                            break
                    elif seg[j] == ')':
                        depth += 1
        return '\\' + ch

    def _needs_rich_rendering(self, content: str) -> bool:
        """True for constructs MarkdownV2 degrades: pipe tables, task lists, <details>, block math.
        Ordinary replies stay on MarkdownV2 so clients render consistent font weight/spacing.

        The rich endpoint is reserved for constructs where raw markdown materially improves output: pipe
        tables (MarkdownV2 has no table syntax and rewrites them into bullet lists), GFM task lists,
        collapsible ``<details>`` blocks, and block math. Adapted from #45995 (@YonganZhang).
        """
        if not content:
            return False
        if any(_TABLE_SEPARATOR_RE.match(line) for line in content.splitlines()):
            return True
        if re.search(r"(?m)^\s*[-*]\s+\[[ xX]\]\s+", content):
            return True
        if re.search(r"(?m)^<details\b|^</details>|^<summary\b|^</summary>", content):
            return True
        return "$$" in content


def telegram_format(message):
    """_telegram_format: ("HTML" or "MarkdownV2", the text Telegram receives)."""
    if _TELEGRAM_FORMAT_HTML.search(message):
        return "HTML", message
    return "MarkdownV2", TelegramAdapter.__new__(TelegramAdapter).format_message(message)


def adapter_send(content):
    """TelegramAdapter.send for one message: rich (raw Markdown) or MarkdownV2."""
    adapter = TelegramAdapter.__new__(TelegramAdapter)
    if adapter._needs_rich_rendering(content):
        return "rich", content
    return "MarkdownV2", adapter.format_message(content)
