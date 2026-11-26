"""Chat replies are plain text: Markdown the model writes is stripped before the reply is sent and saved."""
import pytest

from orchestrator.agent.plain import plain_text
from tests.test_agent_chat import llm_shop, make_shop, message_of, open_chat, saved, say, says, turn, types  # noqa: F401
from tests.test_api_shop import make_shop as shop_factory  # noqa: F401


def test_example_reply_becomes_bullets():
    leaked = ("The Mirelle headphones are available in these colours:\n\n- **Ash Weekend Studio** \u2013 black (the only black model)\n"
              "- **Ash Clean Studio** \u2013 white, brown  \n\nWhich colour would you like?")
    assert plain_text(leaked) == ("The Mirelle headphones are available in these colours:\n\n\u2022 Ash Weekend Studio \u2013 black (the only black model)\n"
                                  "\u2022 Ash Clean Studio \u2013 white, brown\n\nWhich colour would you like?")


@pytest.mark.parametrize("raw, clean", [
    ("See [the shop](https://x.test/shop) now", "See the shop now"),
    ("## Colours\nblack", "Colours\nblack"),
    ("an *italic* word and _not touched_", "an italic word and _not touched_"),
    ("__bold__ and `code`", "bold and code"),
    ("* one\n* two", "\u2022 one\n\u2022 two"),
    ("a\n\n\n\n\nb", "a\n\nb"),
    ("line one  \nline two", "line one\nline two"),
])
def test_markdown_removed(raw, clean):
    assert plain_text(raw) == clean


@pytest.mark.parametrize("text", [
    "3*4 is 12 and 2 * 3 = 6", "T-shirt - blue, size M", "Nike* Air*Max", "a * b", "Great price!\nSecond line", "4*5*6"])
def test_plain_text_untouched(text):
    assert plain_text(text) == text


def test_sent_and_saved_reply_have_no_markdown(make_shop):
    shop, _ = llm_shop(make_shop, says("Colours:\n\n- **Ash Clean Studio** \u2013 white  \n- *Ash Weekend* \u2013 black\n\nWhich one?"))
    headers, ws = open_chat(shop, "U-03")
    say(shop, headers, "what colours?")
    msgs = turn(ws)
    sent = message_of(msgs)["text"]
    cid = next(m for m in msgs if m["type"] == "conversation")["data"]["conversation_id"]
    assert "message" in types(msgs) and "*" not in sent and sent.count("\u2022 ") == 2
    assert saved(shop, "U-03", cid)[1][1] == sent
