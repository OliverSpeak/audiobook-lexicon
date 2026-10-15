import zipfile

import pytest

from audiobook_lexicon import epub
from audiobook_lexicon.lexicon import Entry, Lexicon

XHTML = '''<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><head><title>t</title></head>
<body>{}</body></html>'''

CHAPTER = XHTML.format(
    '<h1>Chapter One</h1>'
    '<p>Hypergamy is real.<sup><a epub:type="noteref" href="notes.xhtml#n1">1</a></sup> See https://www.example.com/a/b for more.</p>'
    '<p>Wordz  with   spaces. The end.Next sentence starts here .</p>'
    '<p>Zorblax and Zorblax and Zorblax again.</p>')
NOTES = XHTML.format('<section epub:type="endnotes"><h1>Notes</h1><p id="n1">1. Source https://example.com/x</p></section>')
OPF = '''<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>T</dc:title><dc:identifier id="id">x</dc:identifier></metadata>
<manifest>
<item id="ch1" href="ch1.xhtml" media-type="application/xhtml+xml"/>
<item id="notes" href="notes.xhtml" media-type="application/xhtml+xml"/>
</manifest>
<spine><itemref idref="ch1"/><itemref idref="notes"/></spine>
</package>'''
CONTAINER = '''<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>'''


@pytest.fixture
def book_path(tmp_path):
    path = tmp_path / 'book.epub'
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('mimetype', 'application/epub+zip')
        z.writestr('META-INF/container.xml', CONTAINER)
        z.writestr('OEBPS/content.opf', OPF)
        z.writestr('OEBPS/ch1.xhtml', CHAPTER)
        z.writestr('OEBPS/notes.xhtml', NOTES)
    return path


@pytest.fixture
def lex(tmp_path):
    lex = Lexicon(tmp_path / 'lexicon')
    lex.set(Entry('Hypergamy', 'high-per-guh-mee', status='verified'))
    return lex


def cleaned(book_path, lex, speller=None, fixes=()):
    book, result = epub.clean_book(book_path, lex, epub.Options(), list(fixes), speller or epub.Speller('xx_none'))
    out = book_path.with_name('out.epub')
    book.save(out)
    with zipfile.ZipFile(out) as z:
        return z, z.namelist(), z.read('OEBPS/ch1.xhtml').decode(), z.read('OEBPS/content.opf').decode(), result


def test_notes_removed_and_text_cleaned(book_path, lex):
    z, names, ch1, opf, result = cleaned(book_path, lex)
    assert 'OEBPS/notes.xhtml' not in names and 'notes.xhtml' not in opf
    assert names[0] == 'mimetype'
    assert '<sup' not in ch1
    assert 'High-per-guh-mee is real. See example.com for more.' in ch1
    assert 'Wordz with spaces. The end. Next sentence starts here.' in ch1
    assert result.stats['footnote markers removed'] == 1


def test_fixes_match_curly_quotes(book_path, lex):
    fixes = epub.load_fixes([])
    import re
    fixes.append(('Wordz', 'Words', re.compile(r'(?<!\w)Wordz(?!\w)')))
    _, _, ch1, _, result = cleaned(book_path, lex, fixes=fixes)
    assert 'Words with' in ch1 and result.stats['typo fixes applied'] == 1


@pytest.mark.skipif(not epub.Speller('en_US'), reason='pyenchant with an en_US dictionary is not installed')
def test_jargon_collected_before_respelling(book_path, lex):
    lex.set(Entry('Zorblax', 'zor-blacks', status='verified'))
    *_, ch1, _, result = cleaned(book_path, lex, speller=epub.Speller('en_US'))
    assert result.jargon['zorblax'] == 3
    assert 'Zor-blacks and Zor-blacks' in ch1


def test_block_text_reads_line_breaks_as_spaces():
    from lxml import etree
    el = etree.fromstring('<p>the first line<br/>and the<!-- c --> second<br/><em>and third</em><code>x.com</code></p>')
    assert epub.block_text(el) == 'the first line and the second and third'


def test_index_with_paragraph_heading_is_dropped():
    from lxml import etree
    root = etree.fromstring(XHTML.format('<div><p><span>INDEX</span></p><p>Abraham</p><p>absence</p></div>').encode())
    assert epub.notes_reason(root) == 'heading "index"'
    root = etree.fromstring(XHTML.format('<p>Index funds are popular, he said, and went on for a while.</p>').encode())
    assert epub.notes_reason(root) is None


@pytest.mark.skipif(not epub.Speller('en_US'), reason='pyenchant with an en_US dictionary is not installed')
def test_words_split_across_line_breaks_are_joined():
    from lxml import etree
    from collections import Counter
    root = etree.fromstring(XHTML.format('<p>a bril <br/>liant idea<br/>and the next line</p>').encode())
    stats = Counter()
    epub.join_split_words(epub.body_of(root), epub.Speller('en_US'), stats)
    assert epub.block_text(epub.body_of(root)[0]) == 'a brilliant idea and the next line'
    assert stats['words split across line breaks joined'] == 1


def test_fixes_rejoin_words_across_line_breaks():
    import re
    from lxml import etree
    from collections import Counter
    root = etree.fromstring(XHTML.format('<p>cannot be de <br/>fended against</p>').encode())
    hits = Counter()
    fixes = [('de fended', 'defended', re.compile('de fended'))]
    epub.join_split_words(epub.body_of(root), epub.Speller('xx_none'), Counter(), fixes, hits)
    assert epub.block_text(epub.body_of(root)[0]) == 'cannot be defended against' and hits['de fended'] == 1


def test_caps_merge_keeps_acronym_entries(lex):
    result = epub.Result()
    result.jargon.update({'PUA': 2, 'BALTASAR': 1, 'baltasar': 3})
    for k, n in result.jargon.items():
        result.jargon_ctx[k] = ''
        result.jargon_forms[k][k] = n
    lex.set(Entry('PUA', 'pee you ay', status='verified'))
    epub.merge_plurals(result, lex)
    assert result.jargon == {'PUA': 2, 'baltasar': 4}
