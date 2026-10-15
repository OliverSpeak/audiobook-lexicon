# audiobook-lexicon

Prepare ebooks for conversion to audiobooks with text-to-speech (TTS), using tools such as
[ebook2audiobook](https://github.com/DrewThomasson/ebook2audiobook).

The problem with converting ebooks is that they are designed to be read, not listened to. They contain things that a
narrator would intuitively work around but a TTS engine reads out regardless, such as:

- references
- indexes
- footnote markers ("as the study showed.¹⁴")
- links
- scanning errors (in the case of OCR'd books)
- and various other leftovers

In addition, it is not uncommon for books to use jargon, coined terms and names.
TTS engines might pronounce these correctly, but they usually don't.

audiobook-lexicon helps bridge this gap:

- **A cleaner** that strips those elements from an EPUB and repairs the text, creating a report of everything it changed and anything worth checking.
- **A pronunciation dataset** of difficult words and how they're actually said (IPA plus a
  plain-English respelling the TTS engine can read with no ambiguity), applied to each book as it's
  cleaned.

Running this with an AI model is recommended. Ebooks will likely contain words unknown to audiobook-lexicon, which need reviewing. An AI model can automate this part by inferring pronunciations from the surrounding context.

`skill/SKILL.md` packages this workflow to streamline the process. It runs the cleaner, researches each todo word, writes entries back with IPA, status
and source, and re-cleans the books.

## The lexicon

Only English is supported. `lexicon/en/` holds one TSV file per first letter (`a.tsv` … `z.tsv`, plus `_.tsv` for
anything else), sorted case-insensitively:

| column | meaning |
|---|---|
| `word` | the word as written. ALL-CAPS acronyms match case-sensitively (`US` never touches "us"); everything else is case-insensitive. Phrases and abbreviations work too (`feme covert`, `Mrs.`). |
| `respelling` | how the TTS should read it: lowercase hyphenated syllables (`post-gres-cue-ell`), or `=` if it already reads correctly |
| `ipa` | IPA, when known, so respellings can be regenerated for other engines |
| `status` | `verified` (dictionary, recording, the person themselves), `guess`, `as-is`, or `todo` |
| `source` | where the pronunciation came from |

One entry covers plurals, possessives and compounds. The tool also restores capitalisation.

## Install

```
uv tool install -e ".[spell]"      # or: pip install -e ".[spell]"
```

The `spell` extra (pyenchant plus a system Hunspell/Enchant dictionary, e.g. `hunspell-en-us`)
helps with jargon detection and spelling checks. Cleaning and respelling still work if you don't want to use that.

## Use

```
audiobook-lexicon clean ebooks/                # clean every EPUB in ebooks/ into ebooks/clean/
audiobook-lexicon clean book.epub --dry-run    # reports only
audiobook-lexicon lookup Hypergamy PUAs        # show entries and how words will be read
audiobook-lexicon set Hypergamy high-per-guh-mee --ipa haɪˈpɜːrɡəmi --status verified --source Wiktionary
audiobook-lexicon set --batch new.tsv          # many entries at once (same columns as the lexicon)
audiobook-lexicon todo                         # entries still needing a pronunciation
audiobook-lexicon remove Ts ao                 # delete entries (e.g. fragments covered by a phrase entry)
audiobook-lexicon validate                     # check the lexicon files (CI runs this)
audiobook-lexicon stats
```

`clean` writes, for each book:

- **a cleaned copy**:
  - drops notes, endnotes, bibliography and index documents
  - removes footnote markers and inline footnotes
  - shortens URLs to their domain (`--urls drop|keep` to change this)
  - fixes spacing and line-break hyphenation
  - applies typo fixes from `<book name>.fixes.txt` lines (`wrong => right`)
  - respells words from the lexicon
- **`<book>.report.txt`**, listing what was removed and fixed, possible typos and other things to review.
- **`jargon_todo.tsv`.** Unknown words seen 3 or more times (`--jargon-min`) are added to the lexicon as `todo`, and this file lists them with context.

## Contributing

Lexicon entries are welcome. Run `audiobook-lexicon validate` and `pytest` before opening a pull request.

## License

MIT
