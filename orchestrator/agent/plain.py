"""Chat replies are shown as plain text; the model still writes Markdown now and then. Strip it, conservatively."""
import re

LINK = re.compile(r"\[([^\]\n]+)\]\([^)\s]*\)")
BULLET = re.compile(r"^[ \t]*[-*][ \t]+(?=\S)", re.M)
BOLD = re.compile(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1")
ITALIC = re.compile(r"(?<![\w*])\*(?=[^\s*])([^*\n]*?[^\s*])\*(?![\w*])")
HEADING = re.compile(r"^[ \t]*#{1,6}[ \t]+", re.M)


def plain_text(text: str) -> str:
    """Markdown out, words kept: bold/italic markers, headings, backticks, links; "- " bullets become "• " lines."""
    text = LINK.sub(r"\1", text)
    text = BULLET.sub("• ", text)
    text = BOLD.sub(r"\2", text)
    text = ITALIC.sub(r"\1", text)
    text = HEADING.sub("", text).replace("`", "")
    text = re.sub(r"[ \t]+$", "", text, flags=re.M)
    return re.sub(r"\n{3,}", "\n\n", text).strip()
