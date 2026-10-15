"""
The pronunciation lexicon: sharded TSV files (lexicon/<lang>/a.tsv ... z.tsv, _.tsv), one entry per line.

Columns:
  word        the word as it appears in text. ALL-CAPS acronyms (SMV, PUA) match case-sensitively;
              everything else matches case-insensitively. May contain spaces or a trailing period
              for phrases and abbreviations ("feme covert", "Mrs.").
  respelling  how the TTS should read it, in lowercase hyphenated syllables ("high-per-guh-mee").
              "=" means the word is already read correctly and is left alone.
  ipa         IPA for the word, if known, so respellings can be regenerated for other engines
  status      verified | guess | as-is | todo
  source      where the pronunciation came from (dictionary, recording, reasoning)
"""

import csv
import re
import unicodedata
from dataclasses import dataclass, fields, astuple
from pathlib import Path

FIELDS = ['word', 'respelling', 'ipa', 'status', 'source']
STATUSES = ('verified', 'guess', 'as-is', 'todo')
AS_IS = '='

# Apostrophe-like marks inside words; texts mix them (Wade-Giles "Ts‘ao Ts’ao", "Ch’in").
APOSTROPHES = "'‘’ʼ"
APOS_RX = re.compile(f"[{APOSTROPHES}]")
# a word starts with a letter, may contain inner apostrophes, and must not follow a letter, digit or apostrophe
TOKEN_RX = re.compile(rf"(?<![\w'’])[^\W\d_](?:[^\W\d_]|[{APOSTROPHES}](?=[^\W\d_]))*")
RESPELLING_RX = re.compile(r"^[a-z' -]+$")
POSSESSIVE_RX = re.compile(r"^(.+?)(['’]s?)$")


def default_root():
    # lexicon/ next to src/ in the repository (editable install or checkout)
    return Path(__file__).resolve().parents[2] / 'lexicon'


def is_acronym(word):
    return re.fullmatch(r"[A-Z0-9]{2,}s?", word) is not None


def is_caps_word(word):
    """An ordinary word set in capitals (a heading's "BALTASAR"), not a short acronym like "DI"."""
    return len(word) >= 4 and is_acronym(word) and word.isalpha()


def key(word):
    """Lookup key: acronyms keep their case so "US" never matches "us"; all apostrophes match."""
    word = APOS_RX.sub("'", word)
    return word if is_acronym(word) else word.casefold()


def shard_of(word):
    first = unicodedata.normalize('NFKD', word)[:1].lower()
    return f'{first}.tsv' if 'a' <= first <= 'z' else '_.tsv'


def is_single_token(word):
    m = TOKEN_RX.fullmatch(word)
    return m is not None


@dataclass
class Entry:
    word: str
    respelling: str = ''
    ipa: str = ''
    status: str = 'todo'
    source: str = ''

    @property
    def spoken(self):
        """The replacement text, or None when the word should be left as it is."""
        if self.status in ('todo', 'as-is') or not self.respelling or self.respelling == AS_IS:
            return None
        return self.respelling


class Lexicon:
    def __init__(self, root=None, lang='en'):
        self.dir = Path(root or default_root()) / lang
        self.entries = {}
        self.problems = []      # (location, message) found while loading
        self._dirty = set()
        self._index = None
        if self.dir.is_dir():
            for path in sorted(self.dir.glob('*.tsv')):
                self._load(path)

    def _load(self, path):
        with path.open(encoding='utf-8', newline='') as f:
            rows = list(csv.reader(f, delimiter='\t', quoting=csv.QUOTE_NONE))
        if not rows or rows[0] != FIELDS:
            self.problems.append((path.name, f'header must be: {chr(9).join(FIELDS)}'))
            rows = rows[1:] if rows and rows[0][:1] == ['word'] else rows
        else:
            rows = rows[1:]
        previous = None
        for n, row in enumerate(rows, 2):
            where = f'{path.name}:{n}'
            if not any(row):
                continue
            if len(row) != len(FIELDS):
                self.problems.append((where, f'expected {len(FIELDS)} tab-separated columns, got {len(row)}'))
                row = (row + [''] * len(FIELDS))[:len(FIELDS)]
            entry = Entry(*[c.strip() for c in row])
            k = key(entry.word)
            if k in self.entries:
                self.problems.append((where, f'duplicate entry for "{entry.word}"'))
            if shard_of(entry.word) != path.name:
                self.problems.append((where, f'"{entry.word}" belongs in {shard_of(entry.word)}'))
            sort_key = (entry.word.casefold(), entry.word)
            if previous and sort_key < previous:
                self.problems.append((where, f'not sorted: "{entry.word}" comes before the previous line'))
            previous = sort_key
            self.entries[k] = entry

    # --- reading -------------------------------------------------------------

    def get(self, word):
        return self.entries.get(key(word))

    def find(self, word):
        """Entry that applies to word: the exact entry, or for ALL-CAPS text of 4+ letters
        ("BALTASAR" in a heading) the ordinary-case entry when there's no acronym entry."""
        entry = self.entries.get(key(word))
        if entry is None and is_caps_word(word):
            entry = self.entries.get(APOS_RX.sub("'", word).casefold())
        return entry

    def __contains__(self, word):
        return key(word) in self.entries

    def __len__(self):
        return len(self.entries)

    def validate(self):
        problems = list(self.problems)
        for entry in self.entries.values():
            where = f'{shard_of(entry.word)}: {entry.word}'
            if entry.status not in STATUSES:
                problems.append((where, f'status must be one of {", ".join(STATUSES)}'))
            if entry.status == 'todo' and entry.respelling:
                problems.append((where, 'todo entries must have an empty respelling'))
            if entry.status != 'todo' and not entry.respelling:
                problems.append((where, 'respelling is empty; use "=" for words read correctly as written'))
            if entry.status == 'as-is' and entry.respelling != AS_IS:
                problems.append((where, 'as-is entries use "=" as the respelling'))
            if entry.respelling and entry.respelling != AS_IS and not RESPELLING_RX.match(entry.respelling):
                problems.append((where, 'respelling may only use lowercase letters, hyphens, spaces and apostrophes'))
        return problems

    # --- writing -------------------------------------------------------------

    def set(self, entry):
        if entry.respelling == AS_IS and entry.status == 'todo':
            entry.status = 'as-is'
        self.entries[key(entry.word)] = entry
        self._dirty.add(shard_of(entry.word))
        self._index = None

    def remove(self, word):
        """Delete an entry; returns it, or None if there was none."""
        entry = self.entries.pop(key(word), None)
        if entry:
            self._dirty.add(shard_of(entry.word))
            self._index = None
        return entry

    def save(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        for shard in sorted(self._dirty):
            rows = sorted((e for e in self.entries.values() if shard_of(e.word) == shard),
                          key=lambda e: (e.word.casefold(), e.word))
            # Written as plain tab-separated lines, matching how load() reads them (no quoting or
            # escaping), so text such as quotes survives a load/save round trip unchanged.
            with (self.dir / shard).open('w', encoding='utf-8', newline='') as f:
                f.write('\t'.join(FIELDS) + '\n')
                for e in rows:
                    f.write('\t'.join(re.sub(r'\s', ' ', str(v)) for v in astuple(e)) + '\n')
        self._dirty.clear()

    # --- applying ------------------------------------------------------------

    def _build_index(self):
        tokens, phrases = {}, []
        for k, e in self.entries.items():
            if e.spoken is None:
                continue
            if is_single_token(e.word):
                tokens[k] = e.spoken
            else:
                flags = 0 if is_acronym(e.word) else re.I
                tail = r"(?!\w)" if e.word[-1].isalnum() else ''
                body = APOS_RX.sub(f"[{APOSTROPHES}]", re.escape(e.word).replace(r"\ ", r"\s+"))
                phrases.append((re.compile(r"(?<!\w)" + body + tail, flags), e.spoken))
        phrases.sort(key=lambda p: -len(p[0].pattern))
        self._index = (tokens, phrases)

    def _lookup(self, word, tokens):
        """Respelling for a word, following plurals and possessives back to their base entry."""
        if (hit := tokens.get(key(word))) is not None:
            return hit
        if is_caps_word(word) and (hit := tokens.get(word.casefold())) is not None:
            return hit  # ALL-CAPS heading text; acronym entries are keyed in capitals, so never hit here
        if m := POSSESSIVE_RX.match(word):
            base = self._lookup(m.group(1), tokens)
            return base + m.group(2) if base else None
        for suffix in ('es', 's'):
            if word.endswith(suffix) and len(word) > len(suffix) + 1:
                if (hit := tokens.get(key(word[:-len(suffix)]))) is not None:
                    return hit + suffix
        return None

    def phrase_spans(self, text):
        """(start, end) of every phrase entry found in text, so its parts aren't flagged as jargon."""
        if self._index is None:
            self._build_index()
        return [m.span() for rx, _ in self._index[1] for m in rx.finditer(text)]

    def respell(self, text):
        """Return (text with respellings applied, number of replacements)."""
        if self._index is None:
            self._build_index()
        tokens, phrases = self._index
        count = 0

        def fit_case(original, spoken):
            if original[:1].isupper() and not is_acronym(original):
                return spoken[:1].upper() + spoken[1:]
            return spoken

        for rx, spoken in phrases:
            text, n = rx.subn(lambda m: fit_case(m.group(0), spoken), text)
            count += n

        def token_repl(m):
            nonlocal count
            word = m.group(0)
            spoken = self._lookup(word, tokens)
            if spoken is None:
                return word
            count += 1
            return fit_case(word, spoken)

        text = TOKEN_RX.sub(token_repl, text)
        return text, count
