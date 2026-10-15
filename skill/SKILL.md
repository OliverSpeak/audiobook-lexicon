---
name: audiobook-lexicon
description: Prepare ebooks for text-to-speech and grow the shared pronunciation lexicon. Runs `audiobook-lexicon clean` on EPUBs (removes notes/footnotes/URLs, fixes spacing and typos, respells words from the lexicon), then researches every word it flags as needing a pronunciation — using web search for names, coinages, acronyms and foreign terms — and records IPA plus a TTS-friendly respelling. Use this whenever the user wants to prep, clean or check an ebook before audiobook conversion, adds a new book, mentions mispronounced words, jargon, pronunciations or the lexicon, or asks to fix how a word is read, even if they don't name the tool.
---

# Ebook prep and the pronunciation lexicon

TTS models read exactly the text they're given and guess at words they haven't seen: coined
jargon, surnames, acronyms, foreign phrases. `audiobook-lexicon` keeps a shared lexicon of respellings
(in the audiobook-lexicon repo, `lexicon/en/*.tsv`, expected to grow to thousands of entries) and applies
it while cleaning each EPUB. The tool finds the words; your job is to work out how each is said and
record it well enough that the entry is useful for every future book.

The user's ebook2audiobook checkout is usually `~/Repositories/ebook2audiobook`, with books in
`ebooks/` and cleaned output in `ebooks/clean/` (the app runs in Docker and sees that folder as
`/app/ebooks/clean/`).

## Workflow

1. **Clean the books** (a folder or specific files):
   ```
   audiobook-lexicon clean ~/Repositories/ebook2audiobook/ebooks/
   ```
   This writes cleaned EPUBs and `<book>.report.txt` to `ebooks/clean/`, adds unknown words seen
   3+ times to the lexicon as `todo`, and writes `ebooks/clean/jargon_todo.tsv`: every todo word
   in these books with its count and a context snippet.

2. **Read `jargon_todo.tsv`.** The context matters: it tells you whether "Dal" is a nickname,
   whether an acronym is spelled out or said as a word, and what language a phrase is in.
   Words that appear only once or twice aren't added automatically; they're listed in each
   report's "possible typos" section. Skim it for genuine typos (step 5) and for anything that
   clearly needs a respelling anyway.

3. **Decide each word** (see "Deciding a pronunciation"). Handle the obvious ones from knowledge;
   spend web searches on the ones you don't know, running independent searches in parallel.

4. **Write the entries** as one batch file (in your scratchpad), then apply it:
   ```
   word	respelling	ipa	status	source
   polygyny	puh-lij-uh-nee	pəˈlɪdʒəni	verified	Merriam-Webster
   Gangestad	gang-uh-stad		guess	Norwegian-origin surname; no recording found
   grift	=		as-is	ordinary English word
   ```
   ```
   audiobook-lexicon set --batch /path/to/batch.tsv
   audiobook-lexicon validate
   ```
   `set` refuses the whole batch if any row is invalid and says why, so fix and rerun.
   - `status`: `verified` when a dictionary, recording or the person themselves backs it;
     `guess` when it's your reasoning; `as-is` (respelling `=`) when the word reads fine as written.
   - `ipa`: fill it in whenever you know it, even for guesses. It's what lets the lexicon serve
     other TTS engines later, so it's worth more than the respelling in the long run.
   - `source`: short and specific ("Merriam-Webster", "author says it in podcast ep. 12",
     "blend of church + Christian"). Other people will audit entries from this.
   - One entry covers plurals and possessives (`PUA` handles "PUAs", "PUA's"). Phrases and
     abbreviations can be entries too (`feme covert`, `Mrs.`).
   - **Names split by hyphens or apostrophes** (Wade-Giles `Sun-tzu`, `Mao Tse-tung`,
     `Ts'ao Ts'ao`) get flagged as fragments ("tzu", "ao", "Ts"). Add the whole name as a phrase
     entry instead, then `audiobook-lexicon remove` the fragment todos: a phrase entry stops its
     parts being flagged, and fragments like "yu" or "da" would be wrong on their own. All
     apostrophe styles (' ‘ ’) match each other.

5. **Typos aren't jargon.** If a flagged word is really a misspelling, mark it `as-is` in the
   lexicon (so it stops being flagged) and add the correction to
   `ebooks/<book name>.fixes.txt` as `wrong => right`. That file is book-specific and stays with
   the book, not in the lexicon.

6. **Re-run step 1** so the cleaned books use the new entries, and check that the todo count for
   these books is 0.

7. **Commit the lexicon** in the audiobook-lexicon repo (`lexicon: add N entries`). Keep book titles out of commit messages and sources: the repo is public, and the entries should stand on their own.
   It's a public repo, so don't push unless the user asks.

8. **Report back** briefly: how many entries you added, the guesses (those are worth listening
   to), and offer a pronunciation check file (below).

When the user says a word is still read wrongly, even an ordinary dictionary word, use
`audiobook-lexicon lookup <word>` to see the current entry and how it's read, then `audiobook-lexicon set` a
better respelling. Dictionary words are never flagged automatically, so user reports are how
those get in.

## Deciding a pronunciation

Work through these in order and stop at the first that applies:

- **Ordinary English that will read correctly**: dictionary words the spellchecker lacked
  (grift, oxytocin) or transparent compounds of common words (failsafe, hindbrain) → `as-is`.
  Unnecessary respellings can only make things worse.
- **Published pronunciation**: Merriam-Webster, Wiktionary, Cambridge, or a Wikipedia IPA
  note. Record the IPA and convert it to a respelling.
- **Names**: search "<name> pronunciation". The person's own usage (interviews, podcasts, talks)
  beats a dictionary. For bands and brands, use how the members or users say it.
- **Coinages** (Fempowerment, Churchian): usually a blend; say it the way its parts are said.
  If the blend is ambiguous, search the term with the author's name; recordings of the author
  settle it.
- **Acronyms**: initialism (letters) or a spoken word? Decide from real usage: MGTOW is "mig-tow",
  SMV is "ess em vee". Write letters out as words.
- **Foreign words**: approximate the native pronunciation in English syllables (Über → oo-ber),
  the way an English-speaking narrator would say it.

If you're still unsure after searching, write your best guess with status `guess`. A guess the
user can review is better than a todo that never gets applied.

## Respelling rules

The respelling is read as plain English text, so write something an English reader would say
correctly on first sight:

- Lowercase syllables joined by hyphens: `guy-no-sen-trizm`. The tool restores the capital
  letter itself when the original word was capitalised. Capitals inside a respelling risk being
  read as letters, and `validate` rejects them.
- Use unambiguous spellings: `ay` (say), `ee` (see), `eye`/`y` (my), `oh` (go), `oo` (food),
  `ow` (cow), `uh` (the unstressed "a" in "about"), `k` for hard c, `j` for soft g, `zh` (measure).
- There's no stress mark. Give the stressed syllable a full vowel and reduce unstressed ones to
  `uh`/`ee`: Hypergamy → `high-per-guh-mee`.
- Stay as close to the real spelling as the sound allows, and use spaces for separate spoken
  words (`ess em vee`, `only fans`).

## Pronunciation check file (optional)

To let the user hear new entries quickly, write `ebooks/clean/pronunciation_check.txt` with one
line per entry added this session, using the respelling:

```
Number one. The word is guy-no-sen-trizm.

Number two. The word is high-per-guh-mee.
```

They convert that small .txt in ebook2audiobook with their usual voice, listen, and tell you
which entries to change.
