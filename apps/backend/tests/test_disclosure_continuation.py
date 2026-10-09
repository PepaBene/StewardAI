"""Text blocks continued by `ix:continuation` (how real filings carry a note) are read whole."""

from steward.disclosure.note_job import block_text
from steward.disclosure.parse import parse_html

HTML = b"""<html><body>
<ix:header></ix:header>
<ix:nonNumeric name="us-gaap:DebtDisclosureTextBlock" contextRef="c1" id="f-1"
 continuedAt="k1"><p>Debt</p></ix:nonNumeric>
<p>unrelated paragraph</p>
<ix:continuation id="k1" continuedAt="k2"><p>Maturities follow.</p></ix:continuation>
<p>another unrelated paragraph</p>
<ix:continuation id="k2"><table><tr><td>2027</td><td>100</td></tr></table></ix:continuation>
</body></html>"""

LOOP = b"""<html><body>
<ix:nonNumeric name="us-gaap:DebtDisclosureTextBlock" contextRef="c1" continuedAt="a">
<p>x</p></ix:nonNumeric>
<ix:continuation id="a" continuedAt="b"><p>one</p></ix:continuation>
<ix:continuation id="b" continuedAt="a"><p>two</p></ix:continuation>
</body></html>"""


def test_a_continued_block_gathers_its_chain_in_order() -> None:
    doc = parse_html(HTML)
    (block,) = doc.text_blocks
    assert len(block.continuations) == 2
    text, to_document = block_text(doc, block)
    assert text.index("Debt") < text.index("Maturities") < text.index("2027")
    assert "unrelated" not in text
    at = text.index("Maturities")
    assert doc.text[to_document(at) : to_document(at) + 10] == "Maturities"
    at = text.index("2027")
    assert doc.text[to_document(at) : to_document(at) + 4] == "2027"


def test_a_continuation_cycle_ends_the_chain() -> None:
    (block,) = parse_html(LOOP).text_blocks
    assert len(block.continuations) == 2


def test_a_block_without_continuation_is_unchanged() -> None:
    doc = parse_html(
        b"<html><body><ix:nonNumeric name='n' contextRef='c'><p>All here</p>"
        b"</ix:nonNumeric></body></html>"
    )
    (block,) = doc.text_blocks
    assert block.continuations == ()
    assert block_text(doc, block)[0].strip() == "All here"
