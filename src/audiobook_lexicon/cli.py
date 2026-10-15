"""audiobook-lexicon: a pronunciation lexicon for audiobook narration, and an EPUB cleaner that applies it."""

import argparse
import collections
import csv
import sys
from pathlib import Path

from . import epub
from .lexicon import AS_IS, FIELDS, STATUSES, Entry, Lexicon, key, shard_of

TODO_FILE = 'jargon_todo.tsv'


def cmd_clean(args, lex):
    books = []
    for p in args.inputs:
        if p.is_dir():
            books += sorted(p.glob('*.epub'))
        elif p.suffix.lower() == '.epub' and p.exists():
            books.append(p)
        else:
            print(f'skipping {p}: not an .epub file or folder', file=sys.stderr)
    if not books:
        sys.exit('no EPUB files found')
    speller = epub.Speller(args.spell_lang)
    if not speller:
        print('note: pyenchant or its dictionary is missing; jargon detection and spelling checks are off', file=sys.stderr)
    opts = epub.Options(urls=args.urls, keep_notes=args.keep_notes, lang=args.spell_lang)
    global_fixes = epub.load_fixes(args.fixes)
    print(f'lexicon: {lex.dir} ({len(lex)} entries)')

    todo = {}       # key -> [word, count, books, context]
    for path in books:
        print(f'\n=== {path.name}')
        fixes = epub.load_fixes([path.with_name(path.stem + '.fixes.txt')]) + global_fixes
        try:
            book, result = epub.clean_book(path, lex, opts, fixes, speller)
        except Exception as e:
            print(f'  ! failed: {e}')
            continue
        outdir = args.outdir or path.parent / 'clean'
        outdir.mkdir(parents=True, exist_ok=True)

        new = 0
        for k, count in result.jargon.most_common():
            entry = lex.find(k)
            if entry is None and count >= args.jargon_min and not args.no_add:
                entry = Entry(epub.display_form(result, k), status='todo', source=f'seen {count}x')
                lex.set(entry)
                new += 1
            if entry is not None and entry.status == 'todo':
                row = todo.setdefault(k, [entry.word, 0, [], result.jargon_ctx[k]])
                row[1] += count
                row[2].append(path.stem)

        report = [f'Report for {path.name}', '', f'Removed from reading order ({len(result.dropped)}):']
        report += [f'  {href}  ({reason})' for href, reason in result.dropped.items()] or ['  none']
        report += ['', 'Automatic fixes:'] + ([f'  {v:5}  {k}' for k, v in result.stats.items() if v] or ['  none'])
        for title, items in result.review.items():
            report += ['', f'Review: {title} ({len(items)})'] + [f'  {i}' for i in items]
        report_path = outdir / (path.stem + '.report.txt')
        report_path.write_text('\n'.join(report) + '\n', encoding='utf-8')
        if not args.dry_run:
            book.save(outdir / path.name)
            print(f'  cleaned -> {outdir / path.name}')
        print('  ' + (', '.join(f'{k}: {v}' for k, v in result.stats.items() if v) or 'no changes'))
        print(f'  report -> {report_path}')
        if new:
            print(f'  {new} new jargon words added to the lexicon as todo')

    lex.save()
    todo_path = (args.outdir or books[0].parent / 'clean') / TODO_FILE
    with todo_path.open('w', encoding='utf-8', newline='') as f:
        w = csv.writer(f, delimiter='\t', lineterminator='\n')
        w.writerow(['word', 'count', 'books', 'context'])
        for word, count, names, context in sorted(todo.values(), key=lambda r: -r[1]):
            w.writerow([word, count, '; '.join(names), context])
    print(f'\n{len(todo)} words in these books still need a pronunciation -> {todo_path}')


def cmd_todo(args, lex):
    rows = sorted((e for e in lex.entries.values() if e.status == 'todo'), key=lambda e: e.word.casefold())
    for e in rows:
        print(f'{e.word}\t{e.source}')
    print(f'{len(rows)} todo', file=sys.stderr)


def cmd_set(args, lex):
    if args.batch:
        with args.batch.open(encoding='utf-8', newline='') as f:
            rows = list(csv.DictReader(f, delimiter='\t', quoting=csv.QUOTE_NONE))
        missing = set(FIELDS) - set(rows[0] if rows else FIELDS)
        if missing:
            sys.exit(f'{args.batch}: missing columns: {", ".join(sorted(missing))}')
        entries = [Entry(**{f: (r.get(f) or '').strip() for f in FIELDS}) for r in rows]
    else:
        if not (args.word and args.respelling):
            sys.exit('give WORD and RESPELLING, or --batch FILE')
        status = args.status or ('as-is' if args.respelling == AS_IS else 'guess')
        entries = [Entry(args.word, args.respelling, args.ipa or '', status, args.source or '')]

    scratch = Lexicon(root=Path('/nonexistent'))     # validate entries on their own first
    for e in entries:
        if e.respelling == AS_IS and e.status in ('', 'todo'):
            e.status = 'as-is'
        scratch.set(e)
    problems = scratch.validate()
    if problems:
        for where, msg in problems:
            print(f'{where}: {msg}', file=sys.stderr)
        sys.exit(f'nothing written: {len(problems)} problem(s)')
    for e in entries:
        old = lex.get(e.word)
        if old and not e.ipa:
            e.ipa = old.ipa                 # keep known IPA when only the respelling changes
        lex.set(e)
    lex.save()
    print(f'{len(entries)} entr{"y" if len(entries) == 1 else "ies"} written')


def cmd_validate(args, lex):
    problems = lex.validate()
    for where, msg in problems:
        print(f'{where}: {msg}')
    counts = collections.Counter(e.status for e in lex.entries.values())
    print(f'{len(lex)} entries ({", ".join(f"{s}: {counts[s]}" for s in STATUSES)}); {len(problems)} problem(s)', file=sys.stderr)
    sys.exit(1 if problems else 0)


def cmd_stats(args, lex):
    counts = collections.Counter(e.status for e in lex.entries.values())
    shards = collections.Counter(shard_of(e.word) for e in lex.entries.values())
    print(f'{len(lex)} entries in {lex.dir}')
    for s in STATUSES:
        print(f'  {s:9} {counts[s]}')
    print('  with IPA  ' + str(sum(1 for e in lex.entries.values() if e.ipa)))
    print('  largest shards: ' + ', '.join(f'{k} ({v})' for k, v in shards.most_common(5)))


def cmd_lookup(args, lex):
    for word in args.words:
        e = lex.find(word)
        spoken, _ = lex.respell(word)
        if e:
            print(f'{e.word}\t{e.respelling or "-"}\t{e.ipa or "-"}\t{e.status}\t{e.source}')
        else:
            print(f'{word}\t(no entry)')
        if spoken != word:
            print(f'  reads as: {spoken}')


def cmd_remove(args, lex):
    for word in args.words:
        e = lex.remove(word)
        print(f'removed {e.word} ({e.status})' if e else f'{word}: no entry')
    lex.save()


def main(argv=None):
    ap = argparse.ArgumentParser(prog='audiobook-lexicon', description=__doc__)
    ap.add_argument('--lexicon-dir', type=Path, help='lexicon root (default: lexicon/ in this repository)')
    ap.add_argument('--lang', default='en', help='lexicon language folder (default: en)')
    sub = ap.add_subparsers(dest='command', required=True)

    p = sub.add_parser('clean', help='clean EPUBs and apply the lexicon')
    p.add_argument('inputs', nargs='+', type=Path, help='EPUB files or folders containing them')
    p.add_argument('--outdir', type=Path, help='where cleaned books and reports go (default: <input dir>/clean)')
    p.add_argument('--fixes', type=Path, action='append', default=[], help='"wrong => right" typo fixes for every book (repeatable)')
    p.add_argument('--urls', choices=('domain', 'drop', 'keep'), default='domain', help='what to do with URLs in the text')
    p.add_argument('--keep-notes', action='store_true', help='keep notes/bibliography documents and footnotes')
    p.add_argument('--jargon-min', type=int, default=3, help='add unknown words seen this many times as todo entries (default: 3)')
    p.add_argument('--no-add', action='store_true', help="don't add new jargon to the lexicon")
    p.add_argument('--spell-lang', default='en_US', help='spellchecker dictionary (default: en_US)')
    p.add_argument('--dry-run', action='store_true', help='write reports and the todo list only, no cleaned EPUBs')
    p.set_defaults(func=cmd_clean)

    p = sub.add_parser('todo', help='list lexicon entries still needing a pronunciation')
    p.set_defaults(func=cmd_todo)

    p = sub.add_parser('set', help='add or update entries')
    p.add_argument('word', nargs='?')
    p.add_argument('respelling', nargs='?', help='lowercase hyphenated syllables, or "=" if it reads fine as written')
    p.add_argument('--ipa')
    p.add_argument('--status', choices=STATUSES, help='default: guess (or as-is for "=")')
    p.add_argument('--source')
    p.add_argument('--batch', type=Path, help=f'TSV file with columns: {" ".join(FIELDS)}')
    p.set_defaults(func=cmd_set)

    p = sub.add_parser('validate', help='check the lexicon files; exits 1 on problems')
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser('stats', help='entry counts')
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser('lookup', help='show entries and how words will be read')
    p.add_argument('words', nargs='+')
    p.set_defaults(func=cmd_lookup)

    p = sub.add_parser('remove', help='delete entries (e.g. todo words that turned out to be fragments)')
    p.add_argument('words', nargs='+')
    p.set_defaults(func=cmd_remove)

    args = ap.parse_args(argv)
    lex = Lexicon(args.lexicon_dir, args.lang)
    args.func(args, lex)


if __name__ == '__main__':
    main()
