"""
Clean an EPUB for text-to-speech: drop notes and reference lists, strip footnote markers,
shorten URLs, fix spacing, apply typo fixes, and respell words from the lexicon.
"""

import collections
import difflib
import posixpath
import re
import sys
import zipfile
from dataclasses import dataclass, field
from urllib.parse import unquote, urlparse

from lxml import etree

from .lexicon import APOS_RX, TOKEN_RX, key

try:
    import enchant
except ImportError:
    enchant = None

NS = {
    'opf': 'http://www.idpf.org/2007/opf',
    'ncx': 'http://www.daisy.org/z3986/2005/ncx/',
    'xhtml': 'http://www.w3.org/1999/xhtml',
    'epub': 'http://www.idpf.org/2007/ops',
    'cnt': 'urn:oasis:names:tc:opendocument:xmlns:container',
}
EPUB_TYPE = f"{{{NS['epub']}}}type"
DROP_TAG = 'ttslex-drop'
UNWRAP_TAG = 'ttslex-unwrap'

NOTES_TYPES = {'footnotes', 'endnotes', 'rearnotes', 'bibliography', 'footnote', 'endnote', 'rearnote',
               'doc-endnotes', 'doc-footnotes', 'doc-bibliography', 'doc-endnote', 'doc-footnote'}
NOTEREF_TYPES = {'noteref', 'doc-noteref'}
NOTES_TITLES = {'notes', 'endnotes', 'footnotes', 'references', 'bibliography', 'works cited',
                'citations', 'sources', 'notes and references', 'source notes', 'selected bibliography',
                'select bibliography', 'further reading', 'index', 'general index'}
SKIP_TEXT_TAGS = {'pre', 'code', 'script', 'style'}
REVIEW_TAGS = ('p', 'li', 'blockquote', 'h1', 'h2', 'h3', 'h4', 'td')

URL_RX = re.compile(r"\b(?:https?://|www\.)[^\s<>\"“”]+", re.I)
FOOTNOTE_MARK_RX = re.compile(r"^\s*[\[(]?(\d{1,3}|[ivx]{1,4}|[*†‡§¶]+)[\])]?\s*$")
INVISIBLE_RX = re.compile('[­​‌‍⁠﻿]')
MULTISPACE_RX = re.compile('(?<=\\S)[  ]{2,}(?=\\S)')
HYPHEN_BREAK_RX = re.compile(r"\b([A-Za-z]{2,})- ([a-z]{2,})\b")
MISSING_SPACE_RX = re.compile(r"([a-z]{2}[’'\"”)]?)([.!?;:,])([A-Z][a-z])")
SPACE_BEFORE_PUNCT_RX = re.compile(r"(\w) ([,;:!?]|\.(?!\s?\.))(?=\s|$|[’\"”)])")
REPEATED_WORD_RX = re.compile(r"\b(\w{2,}) \1\b", re.I)
REPEATED_OK = {'that', 'had', 'is', 'woo', 'bye', 'no', 'yes', 'very', 'so', 'ha', 'tsk', 'knock', 'now', 'go'}
MIXED_RX = re.compile(r"\b(?=\w*\d)(?=\w*[A-Za-z])(?!\d+(?:st|nd|rd|th|s|am|pm|AM|PM|AD|BC|BCE|CE|k|K|m|M|x|D)\b)\w+\b")
PREFIXES = ('un', 'dis', 're', 'de', 'inter', 'intra', 'counter', 'over', 'under', 'hyper', 'mis', 'non', 'pre',
            'post', 'anti', 'neo', 'co', 'ir', 'in', 'im')
SUFFIXES = ('isms', 'ism', 'ists', 'ist', 'ically', 'ally', 'ly', 'ness', 'ments', 'ment', 'able', 'ities', 'ity',
            'ized', 'izing', 'ize', 'ised', 'ising', 'ise', 'al', 'ship', 'hood', 'ic', 's', 'es', 'ed', 'ing', 'er',
            'ers', 'y')


@dataclass
class Options:
    urls: str = 'domain'            # domain | drop | keep
    keep_notes: bool = False
    lang: str = 'en_US'


@dataclass
class Result:
    dropped: dict = field(default_factory=dict)                      # href -> reason
    stats: collections.Counter = field(default_factory=collections.Counter)
    review: dict = field(default_factory=lambda: collections.defaultdict(list))
    jargon: collections.Counter = field(default_factory=collections.Counter)    # key -> count
    jargon_forms: dict = field(default_factory=lambda: collections.defaultdict(collections.Counter))
    jargon_ctx: dict = field(default_factory=dict)                   # key -> context snippet


# --- helpers -------------------------------------------------------------------

def localname(el):
    return etree.QName(el).localname if isinstance(el.tag, str) else None


def semantic_types(el):
    return set((el.get(EPUB_TYPE, '') + ' ' + el.get('role', '')).split())


def text_len(el):
    return len(re.sub(r"\s+", "", ''.join(el.itertext())))


def block_text(el):
    """Text of a block with each <br/> read as a space, as TTS front ends treat line breaks,
    leaving out code (which cleaning doesn't touch)."""
    parts = []

    def walk(e):
        if localname(e) == 'br':
            parts.append(' ')
        parts.append(e.text or '')
        for child in e:
            if isinstance(child.tag, str) and localname(child) not in SKIP_TEXT_TAGS:
                walk(child)     # code is never cleaned or respelled, so it isn't reviewed either
            parts.append(child.tail or '')

    walk(el)
    return ''.join(parts)


def resolve(base_file, href):
    path = unquote(href.split('#', 1)[0])
    if not path or re.match(r"^[a-z]+:", path, re.I):
        return None
    return posixpath.normpath(posixpath.join(posixpath.dirname(base_file), path))


def ctx(text, start, end, pad=45):
    return '…' + re.sub(r"\s+", " ", text[max(0, start - pad):end + pad]) + '…'


def body_of(root):
    body = root.find('xhtml:body', NS)
    return body if body is not None else root


def in_skipped(el):
    return el is not None and (localname(el) in SKIP_TEXT_TAGS or any(localname(a) in SKIP_TEXT_TAGS for a in el.iterancestors()))


def iter_text_slots(body):
    """Yield (element, attr) for every text/tail string outside pre/code/script/style."""
    for el in body.iter():
        if not isinstance(el.tag, str):
            if el.tail and not in_skipped(el.getparent()):
                yield el, 'tail'
            continue
        if el.text and not in_skipped(el):
            yield el, 'text'
        if el is not body and el.tail and not in_skipped(el.getparent()):
            yield el, 'tail'


def load_fixes(paths):
    """Read `wrong => right` lines; straight and curly quotes match each other."""
    fixes = []
    for p in paths:
        if not p.exists():
            continue
        for n, line in enumerate(p.read_text(encoding='utf-8').splitlines(), 1):
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if '=>' not in line:
                print(f'  ! {p.name}:{n}: expected "wrong => right", skipped', file=sys.stderr)
                continue
            wrong, right = (s.strip() for s in line.split('=>', 1))
            pattern = re.escape(wrong).replace("'", "['’]").replace('"', '["“”]')
            fixes.append((wrong, right, re.compile(r"(?<!\w)" + pattern + r"(?!\w)")))
    return fixes


class Speller:
    """Dictionary checks (pyenchant). Inactive when pyenchant or the dictionary is missing."""

    def __init__(self, lang):
        self.dicts = []
        if enchant is None:
            return
        tags = [lang] + ([t for t in ('en_GB', 'en_US', 'en_AU', 'en_CA') if t != lang] if lang.startswith('en') else [])
        for tag in tags:
            try:
                if enchant.dict_exists(tag):
                    self.dicts.append(enchant.Dict(tag))
            except Exception:
                pass

    def __bool__(self):
        return bool(self.dicts)

    def known(self, word):
        word = word.replace('’', "'")
        return any(d.check(word) or d.check(word.lower()) for d in self.dicts)

    def derived(self, word):
        """Built from a known word plus a common prefix/suffix: usually a coinage that reads fine."""
        w = word.lower()
        for pre in PREFIXES:
            if w.startswith(pre) and len(w) - len(pre) >= 4 and self.known(w[len(pre):]):
                return True
        for suf in SUFFIXES:
            stem = w[:-len(suf)]
            if w.endswith(suf) and len(stem) >= 4 and (self.known(stem) or self.known(stem + 'e') or self._derived_stem(stem)):
                return True
        return False

    def _derived_stem(self, stem):
        return any(stem.endswith(s) and len(stem) - len(s) >= 4 and self.known(stem[:-len(s)]) for s in SUFFIXES)

    def suggest(self, word):
        return self.dicts[0].suggest(word)[:3] if self.dicts else []


# --- the book ------------------------------------------------------------------

class Book:
    def __init__(self, path):
        self.path = path
        with zipfile.ZipFile(path) as z:
            self.names = z.namelist()
            self.files = {n: z.read(n) for n in self.names}
        container = etree.fromstring(self.files['META-INF/container.xml'])
        self.opf_path = container.find('.//cnt:rootfile', NS).get('full-path')
        self.opf = etree.fromstring(self.files[self.opf_path])
        self.manifest = {}
        for item in self.opf.find('opf:manifest', NS):
            if localname(item) == 'item':
                self.manifest[item.get('id')] = (resolve(self.opf_path, item.get('href')), item.get('media-type'), item)
        self.spine = [ir.get('idref') for ir in self.opf.find('opf:spine', NS) if localname(ir) == 'itemref']
        self.trees = {}

    def tree(self, name):
        if name not in self.trees:
            parser = etree.XMLParser(resolve_entities=False, huge_tree=True, remove_blank_text=False)
            self.trees[name] = etree.fromstring(self.files[name], parser)
        return self.trees[name]

    def spine_docs(self):
        for idref in self.spine:
            href, mtype, _ = self.manifest.get(idref, (None, None, None))
            if href in self.files and mtype in ('application/xhtml+xml', 'text/html'):
                yield idref, href

    def all_docs(self):
        for href, mtype, _ in self.manifest.values():
            if href in self.files and mtype in ('application/xhtml+xml', 'text/html'):
                yield href

    def remove_doc(self, idref, href):
        """Take a document out of the spine, manifest, guide, NCX and the zip."""
        spine = self.opf.find('opf:spine', NS)
        for ir in list(spine):
            if ir.get('idref') == idref:
                spine.remove(ir)
        item = self.manifest[idref][2]
        item.getparent().remove(item)
        self.files.pop(href, None)
        self.trees.pop(href, None)

    def prune_navigation(self, removed):
        guide = self.opf.find('opf:guide', NS)
        if guide is not None:
            for ref in list(guide):
                if resolve(self.opf_path, ref.get('href', '')) in removed:
                    guide.remove(ref)
        for href, mtype, _ in self.manifest.values():
            if href in self.files and mtype == 'application/x-dtbncx+xml':
                for nav_point in list(self.tree(href).iter(f"{{{NS['ncx']}}}navPoint")):
                    srcs = {resolve(href, c.get('src', '')) for c in nav_point.iter(f"{{{NS['ncx']}}}content")}
                    if srcs and srcs <= set(removed) and nav_point.getparent() is not None:
                        nav_point.getparent().remove(nav_point)

    def save(self, out_path):
        for name, tree in self.trees.items():
            self.files[name] = etree.tostring(tree, xml_declaration=True, encoding='utf-8')
        self.files[self.opf_path] = etree.tostring(self.opf, xml_declaration=True, encoding='utf-8')
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(out_path, 'w') as z:
            if 'mimetype' in self.files:
                z.writestr(zipfile.ZipInfo('mimetype'), self.files['mimetype'], compress_type=zipfile.ZIP_STORED)
            for name in self.names:
                if name != 'mimetype' and name in self.files:
                    z.writestr(name, self.files[name], compress_type=zipfile.ZIP_DEFLATED)


def notes_reason(root):
    """Why a document is a notes/reference section, or None if it's ordinary text."""
    body = body_of(root)
    total = text_len(body)
    if total == 0:
        return None
    notes = 0
    for el in body.iter():
        if isinstance(el.tag, str) and semantic_types(el) & NOTES_TYPES:
            if not any(semantic_types(a) & NOTES_TYPES for a in el.iterancestors()):
                notes += text_len(el)
    if notes >= 0.6 * total:
        return 'marked as notes/bibliography'
    # the first heading, or a short first paragraph (converted books often style headings as <p>)
    for el in body.iter():
        name = localname(el)
        if name in ('h1', 'h2', 'h3') or (name == 'p' and 0 < text_len(el) <= 40):
            heading = re.sub(r"[^a-z ]", "", ' '.join(''.join(el.itertext()).lower().split()))
            if heading in NOTES_TITLES:
                return f'heading "{heading}"'
            break
        if name == 'p' and text_len(el):
            break
    text = ''.join(body.itertext())
    urls = URL_RX.findall(text)
    if len(urls) >= 3 and sum(map(len, urls)) > 0.12 * len(re.sub(r"\s+", "", text)):
        return f'{len(urls)} URLs (reference list)'
    return None


def strip_note_markup(root, href, dropped, keep_notes, stats):
    """Remove footnote markers and inline notes; unwrap links into removed documents."""
    body = body_of(root)
    is_nav = root.find('.//xhtml:nav', NS) is not None
    changed = False
    for el in body.iter():
        if not isinstance(el.tag, str) or el.getparent() is None:
            continue
        if el.tag in (DROP_TAG, UNWRAP_TAG) or any(a.tag == DROP_TAG for a in el.iterancestors()):
            continue    # already handled through an enclosing element (<sup><a noteref>)
        name, types = localname(el), semantic_types(el)
        target = resolve(href, el.get('href', '')) if name == 'a' else None
        if types & NOTEREF_TYPES or (name == 'a' and target in dropped and not is_nav):
            parent = el.getparent()
            if not types & NOTEREF_TYPES and not FOOTNOTE_MARK_RX.match(''.join(el.itertext())):
                el.tag = UNWRAP_TAG          # ordinary link into removed notes: keep its text
                stats['links into removed notes unwrapped'] += 1
            elif localname(parent) == 'sup' and ''.join(parent.itertext()).strip() == ''.join(el.itertext()).strip():
                parent.tag = DROP_TAG
                stats['footnote markers removed'] += 1
            else:
                el.tag = DROP_TAG
                stats['footnote markers removed'] += 1
            changed = True
        elif name == 'sup' and FOOTNOTE_MARK_RX.match(''.join(el.itertext())):
            # only a marker right after a word or punctuation, so exponents like 10<sup>3</sup> survive
            prev = el.getprevious()
            before = (el.getparent().text or '') if prev is None else ''.join(prev.itertext()) + (prev.tail or '')
            if re.search(r"[A-Za-z.,;:!?’\"”)\]]\s*$", before):
                el.tag = DROP_TAG
                stats['footnote markers removed'] += 1
                changed = True
        elif types & NOTES_TYPES and name in ('aside', 'div', 'section', 'p', 'span', 'li', 'ol', 'ul') and not keep_notes:
            el.tag = DROP_TAG
            stats['inline footnotes removed'] += 1
            changed = True
        elif name == 'a' and is_nav and target in dropped:
            li = next((a for a in el.iterancestors() if localname(a) == 'li'), None)
            if li is not None:
                li.tag = DROP_TAG
                changed = True
    if changed:
        etree.strip_elements(root, DROP_TAG, with_tail=False)
        etree.strip_tags(root, UNWRAP_TAG)
    return is_nav


def clean_text(text, opts, speller, fixes, fixes_hit, stats, review, href):
    text, n = INVISIBLE_RX.subn('', text)
    stats['invisible characters removed'] += n
    if opts.urls != 'keep':
        def url_repl(m):
            url = m.group(0).rstrip('.,;:)]}’”')
            trail = m.group(0)[len(url):]
            if opts.urls == 'drop':
                return trail
            netloc = urlparse(url if '://' in url else 'http://' + url).netloc
            return re.sub(r"^www\.", "", netloc) + trail
        text, n = URL_RX.subn(url_repl, text)
        stats[f'URLs shortened ({opts.urls})'] += n
    text, n = MULTISPACE_RX.subn(' ', text)
    stats['repeated spaces collapsed'] += n
    if opts.lang.lower().startswith('en'):
        if speller:
            def hyphen_repl(m):
                a, b = m.group(1), m.group(2)
                if speller.known(a + b) and not (speller.known(a) and speller.known(b)):
                    stats['line-break hyphenation joined'] += 1
                    return a + b
                review['possible broken hyphenation (check)'].append(f'[{href}] {ctx(text, m.start(), m.end())}')
                return m.group(0)
            text = HYPHEN_BREAK_RX.sub(hyphen_repl, text)
        text, n = MISSING_SPACE_RX.subn(r"\1\2 \3", text)
        stats['missing space after punctuation added'] += n
        text, n = SPACE_BEFORE_PUNCT_RX.subn(r"\1\2", text)
        stats['space before punctuation removed'] += n
    for wrong, right, rx in fixes:
        text, n = rx.subn(lambda m: right.replace("'", '’') if '’' in m.group(0) else right, text)
        fixes_hit[wrong] += n
    return text


def review_text(t, el, href, speller, result, unknown, unknown_ctx, phrase_spans=()):
    """Collect review items and jargon candidates from one block of (pre-respelling) text."""
    for m in REPEATED_WORD_RX.finditer(t):
        if m.group(1).lower() not in REPEATED_OK:
            result.review['repeated words'].append(f'[{href}] {ctx(t, m.start(), m.end())}')
    for m in MIXED_RX.finditer(t):
        result.review['letters+digits mixed (footnote/OCR leftovers?)'].append(f'[{href}] {ctx(t, m.start(), m.end())}')
    if not speller:
        return
    for m in TOKEN_RX.finditer(t):
        if any(a <= m.start() and m.end() <= b for a, b in phrase_spans):
            continue  # part of a phrase entry such as "Sun-tzu"
        w = APOS_RX.sub("'", m.group(0))
        if w.endswith("'s"):
            w = w[:-2]
        if len(w) < 2 or speller.known(w) or speller.derived(w):
            continue
        k = key(w)
        result.jargon[k] += 1
        result.jargon_forms[k][w] += 1
        result.jargon_ctx.setdefault(k, f'[{href}] {ctx(t, m.start(), m.end())}')
        hyphenated = t[m.start() - 1:m.start()] == '-' or t[m.end():m.end() + 1] == '-'
        if localname(el) == 'p' and w[0].islower() and len(w) > 2 and not hyphenated:
            unknown[w] += 1
            unknown_ctx.setdefault(w, f'[{href}] {ctx(t, m.start(), m.end())}')


def join_split_words(body, speller, stats, fixes=(), fixes_hit=None):
    """Rejoin words an OCR'd book split across a line break ("bril <br/>liant"), which TTS
    would read as two fragments with a pause. Joins when the result is a known word and a half
    isn't, or when the book's fixes list the pair ("de fended => defended")."""
    pairs = {wrong: right for wrong, right, _ in fixes if wrong.count(' ') == 1}
    if not speller and not pairs:
        return
    for br in list(body.iter(f'{{{NS["xhtml"]}}}br')):
        prev, parent = br.getprevious(), br.getparent()
        before = (prev.tail if prev is not None else parent.text) or ''
        after = br.tail or ''
        a = re.search(r"(?<![\w'’-])([^\W\d_]+) ?$", before)
        b = re.match(r"([^\W\d_]+)(?!\w)", after)
        if not (a and b and b.group(1).islower()):
            continue
        left, right = a.group(1), b.group(1)
        fix = pairs.get(f'{left} {right}')
        if fix is not None:
            fixes_hit[f'{left} {right}'] += 1
            word = fix
        elif speller and speller.known(left + right) and not (speller.known(left) and speller.known(right)):
            stats['words split across line breaks joined'] += 1
            word = left + right
        else:
            continue
        joined = before[:a.start(1)] + word + after[len(right):]
        if prev is not None:
            prev.tail = joined
        else:
            parent.text = joined
        parent.remove(br)


def clean_book(path, lexicon, opts, fixes, speller):
    """Clean one book in memory. Returns (Book, Result); call Book.save() to write it."""
    book = Book(path)
    result = Result()
    stats, review = result.stats, result.review

    if not opts.keep_notes:
        for idref, href in list(book.spine_docs()):
            try:
                reason = notes_reason(book.tree(href))
            except etree.XMLSyntaxError as e:
                review['unparseable documents (left untouched)'].append(f'{href}: {e}')
                continue
            if reason:
                result.dropped[href] = reason
                book.remove_doc(idref, href)
        if result.dropped:
            book.prune_navigation(result.dropped)

    unknown, unknown_ctx = collections.Counter(), {}
    fixes_hit = collections.Counter()
    for href in list(book.all_docs()):
        try:
            root = book.tree(href)
        except etree.XMLSyntaxError:
            continue
        body = body_of(root)
        is_nav = strip_note_markup(root, href, result.dropped, opts.keep_notes, stats)
        join_split_words(body, speller, stats, fixes, fixes_hit)
        for el, attr in iter_text_slots(body):
            orig = getattr(el, attr)
            text = clean_text(orig, opts, speller, fixes, fixes_hit, stats, review, href)
            if text != orig:
                setattr(el, attr, text)
        if is_nav:
            continue
        for el in body.iter():
            # outermost block only, so text inside <blockquote><p> is counted once
            if localname(el) in REVIEW_TAGS and not any(localname(a) in REVIEW_TAGS for a in el.iterancestors()):
                t = block_text(el)
                review_text(t, el, href, speller, result, unknown, unknown_ctx, lexicon.phrase_spans(t))
        # respell last, so the checks above saw the original words
        for el, attr in iter_text_slots(body):
            text, n = lexicon.respell(getattr(el, attr))
            if n:
                setattr(el, attr, text)
                stats['words respelled from lexicon'] += n

    for wrong, right, _ in fixes:
        if fixes_hit[wrong]:
            stats['typo fixes applied'] += fixes_hit[wrong]
        else:
            review['fixes not found (text may span formatting tags)'].append(f'{wrong} => {right}')

    typos = []
    for w, c in unknown.items():
        if c > 2 or w in lexicon:
            continue
        sugg = speller.suggest(w)
        if any(s.lower() == w.lower() for s in sugg):
            continue  # only capitalisation differs (luddites/Luddites)
        score = max((difflib.SequenceMatcher(None, w.lower(), s.lower()).ratio()
                     for s in sugg if ' ' not in s and '-' not in s), default=0)
        typos.append((score, f'{c}x {w:22} -> {", ".join(sugg) or "?":40} {unknown_ctx[w]}'))
    if typos:
        # closest-to-a-real-word first: those are the likeliest genuine typos
        review['possible typos (rare unknown words, likeliest first)'] = [line for _, line in sorted(typos, key=lambda x: -x[0])]

    merge_plurals(result, lexicon)
    return book, result


def merge_plurals(result, lexicon):
    """Fold plurals into their singular (PUAs -> PUA), and ALL-CAPS forms into the ordinary
    spelling (BALTASAR -> Baltasar), so each gets one lexicon entry."""
    for k in list(result.jargon):
        lower = k.casefold()
        if k != lower and lexicon.get(k) is None and (lower in result.jargon or lexicon.find(k)):
            result.jargon[lower] += result.jargon.pop(k)
            result.jargon_forms[lower].update(result.jargon_forms.pop(k))
            result.jargon_ctx.setdefault(lower, result.jargon_ctx.pop(k))
    for k in list(result.jargon):
        for suffix in ('es', 's'):
            stem = k[:-len(suffix)]
            if k.endswith(suffix) and stem and (stem in result.jargon or stem in lexicon):
                result.jargon[stem] += result.jargon.pop(k)
                result.jargon_forms[stem].update(result.jargon_forms.pop(k))
                result.jargon_ctx.setdefault(stem, result.jargon_ctx.pop(k))
                break


def display_form(result, k):
    """The spelling to use for a new entry: the most common form that matches the key itself."""
    forms = [f for f, _ in result.jargon_forms[k].most_common() if key(f) == k]
    return forms[0] if forms else result.jargon_forms[k].most_common(1)[0][0]
