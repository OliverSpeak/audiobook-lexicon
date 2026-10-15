import pytest

from audiobook_lexicon.lexicon import Entry, Lexicon, shard_of


@pytest.fixture
def lex(tmp_path):
    lex = Lexicon(tmp_path)
    for e in [
        Entry('Hypergamy', 'high-per-guh-mee', status='verified'),
        Entry('Manosphere', 'man-oh-sphere', status='verified'),
        Entry('PUA', 'pee you ay', status='verified'),
        Entry('US', 'you ess', status='verified'),
        Entry('Mrs.', 'missus', status='verified'),
        Entry('feme covert', 'fem kuv-ert', status='verified'),
        Entry('Dalrock', '=', status='as-is'),
        Entry('Tomassi', status='todo'),
    ]:
        lex.set(e)
    lex.save()
    return Lexicon(tmp_path)


def respell(lex, text):
    return lex.respell(text)[0]


def test_case_insensitive_and_capitalised(lex):
    assert respell(lex, 'hypergamy and Hypergamy') == 'high-per-guh-mee and High-per-guh-mee'


def test_possessive_plural_and_compound(lex):
    assert respell(lex, 'Hypergamy’s reach') == 'High-per-guh-mee’s reach'
    assert respell(lex, 'the PUAs said') == 'the pee you ays said'
    assert respell(lex, 'the Christian-Manosphere') == 'the Christian-Man-oh-sphere'


def test_acronyms_are_case_sensitive(lex):
    assert respell(lex, 'the US told us') == 'the you ess told us'


def test_phrases_and_abbreviations(lex):
    assert respell(lex, 'Mrs. Smith, a feme  covert') == 'Missus Smith, a fem kuv-ert'


def test_as_is_and_todo_are_not_applied(lex):
    assert respell(lex, 'Dalrock met Tomassi') == 'Dalrock met Tomassi'


def test_partial_words_untouched(lex):
    assert respell(lex, 'Hypergamyx 16th PUAx') == 'Hypergamyx 16th PUAx'


def test_saved_sorted_and_sharded(lex, tmp_path):
    assert shard_of('Über') == 'u.tsv' and shard_of('3D') == '_.tsv'
    lines = (tmp_path / 'en' / 'm.tsv').read_text(encoding='utf-8').splitlines()
    assert lines[0].split('\t') == ['word', 'respelling', 'ipa', 'status', 'source']
    assert [l.split('\t')[0] for l in lines[1:]] == ['Manosphere', 'Mrs.']
    assert lex.validate() == []


def test_validate_reports_problems(tmp_path):
    shard = tmp_path / 'en' / 'a.tsv'
    shard.parent.mkdir(parents=True)
    shard.write_text('word\trespelling\tipa\tstatus\tsource\n'
                     'zeta\tzay-tuh\t\tverified\t\n'        # wrong shard
                     'apple\tApple\t\tverified\t\n'          # capitals in respelling
                     'abc\t\t\tguess\t\n', encoding='utf-8')  # empty respelling
    messages = ' '.join(msg for _, msg in Lexicon(tmp_path).validate())
    assert 'belongs in z.tsv' in messages
    assert 'not sorted' in messages
    assert 'lowercase letters' in messages
    assert 'respelling is empty' in messages


def test_save_round_trips_quotes_and_backslashes(tmp_path):
    lex = Lexicon(tmp_path)
    lex.set(Entry('Alles', 'al-us', status='verified', source='German "alles" \\ über alles'))
    lex.save()
    for _ in range(2):
        lex = Lexicon(tmp_path)
        lex._dirty.add(shard_of('Alles'))
        lex.save()
    assert Lexicon(tmp_path).get('Alles').source == "German 'alles' \\ über alles"
    assert not Lexicon(tmp_path).validate()


def test_apostrophe_variants_match(tmp_path):
    lex = Lexicon(tmp_path)
    lex.set(Entry("Ts'ai", 'tsy', status='guess'))
    lex.set(Entry("Ts'ao Ts'ao", 'tsow tsow', status='guess'))
    lex.set(Entry('Sun-tzu', 'soon dzuh', status='guess'))
    assert respell(lex, 'Ch‘u-Ts’ai and Ts‘ao Ts’ao read Sun-tzu.') == 'Ch‘u-Tsy and Tsow tsow read Soon dzuh.'
    assert lex.phrase_spans('read Sun-tzu') == [(5, 12)]


def test_remove(lex, tmp_path):
    assert lex.remove('tomassi').word == 'Tomassi'
    lex.save()
    assert Lexicon(tmp_path).get('Tomassi') is None and Lexicon(tmp_path).get('Dalrock')


def test_all_caps_falls_back_to_ordinary_entry(lex):
    lex.set(Entry('Baltasar', 'bal-tah-sar', status='verified'))
    assert respell(lex, 'BALTASAR GRACIAN and the US') == 'bal-tah-sar GRACIAN and the you ess'
    lex.set(Entry('di', 'dee', status='verified'))
    assert respell(lex, 'the DI and di') == 'the DI and dee'
    assert lex.find('BALTASAR').word == 'Baltasar' and lex.find('us') is None
