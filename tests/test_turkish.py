"""A call in Turkish: what the confirmation gate reads as a yes, a no and a read-back, how a written amount is
read in the Turkish and in the English form, and the two inputs of a run that a Turkish final can differ in
(the address of the bank, the encoding of a file).

Offline. The conversations are played by hand against the compiled workflow and the stand-in bank. They show
what the gate does with words it is given; nothing here shows what a Turkish phone agent or a speech
recogniser says, and no call was made in Turkish on the platform.
"""
import datetime
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from benchmarks.harness.fixture_spec import spec as fixture
from builder import run as builder_run
from builder.config import settings
from builder.runtime import runtime as rt
from tests.support import KIT, refusal
from tests.test_m18_confirmation import D15, READ_BACK, call, check, create, recorded

DAY = datetime.date(2026, 10, 15)          # a Thursday
READ_BACK_TR = 'Kredi kartınız için 500 TL, 15 Ekim Perşembe günü. Onaylıyor musunuz?'


class TheWords(unittest.TestCase):
    def test_a_turkish_yes_is_a_yes(self):
        for said in ('Evet, onaylıyorum.', 'Evet.', 'EVET', 'evet onayliyorum', 'Tamam, kabul ediyorum.', 'Onaylıyorum', 'Tabii, olur.', 'Evet, doğru.', 'Peki, tamamdır.', 'İyi, tamam.',
                     'Evet, 500 lira, 15 Ekim.', 'Evet, perşembe günü.'):
            self.assertEqual(rt.reply_problem([said], 500.0, DAY, READ_BACK_TR), '', said)

    def test_a_no_a_doubt_a_condition_or_a_change_is_never_a_yes(self):
        for said in ('Hayır.', 'Hayır, onaylamıyorum.', 'Onaylamıyorum.', 'Evet ama önce eşime sorayım.', 'Evet, fakat cuma olsun.', 'Tamam değil.', 'Evet... aslında emin değilim.', 'Belki.',
                     'Evet, ama 400 lira olsun.', 'Evet, 400 lira.', 'Evet, 16 Ekim.', 'Evet, kasım ayında.', 'Evet, cuma günü.', 'Kabul etmiyorum.', 'Evet demedim.', 'Olmaz.', 'Bilmiyorum.',
                     'Evet mi dedim?', 'Tamam mı', 'İstemiyorum, evet.', 'Evet, ödeyemeyeceğim.', 'Onaylamam.', 'Anladım.', 'Hı hı.', ''):
            self.assertNotEqual(rt.reply_problem([said], 500.0, DAY, READ_BACK_TR), '', said)
        self.assertEqual(rt.reply_problem(['Evet.', 'Hayır, vazgeçtim.'], 500.0, DAY, READ_BACK_TR), 'the person refused, hesitated or changed something', 'a later no takes the yes back')

    def test_english_is_read_as_before(self):
        for said in ('Yes, I confirm.', 'Yeah, okay.', 'Correct.', 'Yes, 500 on Thursday.'):
            self.assertEqual(rt.reply_problem([said], 500.0, DAY, READ_BACK), '', said)
        for said, why in (('Sure, why not?', 'the person asked something'), ("Yes but I can't.", 'the person refused, hesitated or changed something'), ('Hello.', 'the answer was not a clear yes'),
                          ('Yes, on Friday.', 'the person named another day'), ('Yes, 400.', 'the person named another amount or day'), ('Yes, in November.', 'the person named another month'),
                          ('Maybe.', 'the answer was not a clear yes'), ("I don't know.", 'the answer was not a clear yes')):
            self.assertEqual(rt.reply_problem([said], 500.0, DAY, READ_BACK), why, said)
        self.assertEqual(rt.words_of("I don't know, it's FINE"), ['i', 'dont', 'know', 'its', 'fine'])

    def test_words_are_whole_words_however_the_letters_were_typed(self):
        self.assertEqual(rt.words_of('Hayır, değiştirmek İSTİYORUM; çağrı şüphe ILIK'), ['hayir', 'degistirmek', 'istiyorum', 'cagri', 'suphe', 'ilik'])
        self.assertEqual(rt.words_of('hayir'), rt.words_of('HAYIR'), 'typed without the Turkish letters, or shouted')
        self.assertEqual(rt.plain('İstanbul İğne'), 'istanbul igne')

    def test_a_read_back_in_turkish_is_a_read_back(self):
        for said in (READ_BACK_TR, '500,00 TL, 15 Ekim 2026.', '500 lira, 15 EKİM', 'Tutar 500 TL, tarih 2026-10-15.'):
            self.assertTrue(rt.says(said, 500.0, DAY), said)
        for said in ('500 TL, 16 Ekim.', '400 TL, 15 Ekim.', '500 TL, 15 Kasım.', '500 TL, perşembe günü.', 'Beş yüz lira, 15 Ekim.'):
            self.assertFalse(rt.says(said, 500.0, DAY), said)
        self.assertTrue(rt.says('1.500 TL, 15 Ekim', 1500.0, DAY), 'fifteen hundred, as it is written in Turkish')
        self.assertTrue(rt.says('1.500,00 TL, 15 Ekim', 1500.0, DAY))
        self.assertTrue(rt.says(READ_BACK, 500.0, DAY), 'the English read-back as before')


class TheAmounts(unittest.TestCase):
    def test_the_turkish_and_the_english_form_of_a_number(self):
        for written, value in (('1.500,00', 1500.0), ('1500,50', 1500.5), ('2.250,75', 2250.75), ('1.234.567,89', 1234567.89), ('0,5', 0.5), ('12,5', 12.5), ('1.500.000', 1500000.0),
                               ('1,500.00', 1500.0), ('1,500', 1500.0), ('1,234,567.89', 1234567.89), ('1500.50', 1500.5), ('1500', 1500.0), ('500.00', 500.0), ('0.5', 0.5), (' 42 ', 42.0),
                               ('-5', -5.0), (1500, 1500.0), (12.5, 12.5)):
            self.assertEqual(rt.number(written), value, written)

    def test_what_cannot_be_read_for_sure_is_not_guessed(self):
        for written in ('1,5000', '12,3456', '1.50.00', '1,2,3', 'abc', '', None, True, '1.500,00,00'):
            self.assertIsNone(rt.number(written), written)
        self.assertEqual(rt.number('1.500'), 1.5, 'one dot and three digits stays what it was for a value a tool is given')
        self.assertEqual(rt.readings('1.500'), [1.5, 1500.0], 'in speech it can be either, and the gate looks for the amount it knows')
        self.assertEqual(rt.readings('1.50'), [1.5])
        self.assertEqual(rt.numbers_of('1.500,00 TL ve 15 Ekim, 2.250,75.'), [1500.0, 15.0, 2250.75])
        self.assertEqual(rt.numbers_of('500.00 on Thursday 15 October, card ending 4821.'), [500.0, 15.0, 4821.0])

    def test_an_amount_the_person_repeats_in_the_other_form_is_no_other_amount(self):
        self.assertEqual(rt.reply_problem(['Evet, 1.500 lira.'], 1500.0, DAY, '1500 TL, 15 Ekim'), '')
        self.assertEqual(rt.reply_problem(['Evet, 1.500,00 TL.'], 1500.0, DAY, '1.500 TL, 15 Ekim'), '')
        self.assertNotEqual(rt.reply_problem(['Evet, 1.400 lira.'], 1500.0, DAY, '1.500 TL, 15 Ekim'), '')


class TheGateInACall(unittest.TestCase):
    """The compiled workflow and the stand-in bank, with the conversation in Turkish."""

    def turkish(self):
        return dict(fixture(True), language='Turkish')

    def test_a_promise_is_recorded_after_a_turkish_read_back_and_a_turkish_yes(self):
        api, sim, bank = call(self.turkish())
        sim.hear('Yarın ödeyebilirim.')
        check(api, sim)
        sim.say(READ_BACK_TR)
        sim.hear('Evet, onaylıyorum.')
        self.assertTrue(recorded(create(api, sim)))
        self.assertEqual(bank.ledger['promises'], [{'product_id': 'p1', 'amount': 500.0, 'date': D15}])

    def test_nothing_is_recorded_after_a_turkish_no_or_a_conditional_yes(self):
        for said, why in (('Hayır, onaylamıyorum.', 'the answer was not a clear yes'), ('Evet ama cuma günü olsun.', 'the person refused, hesitated or changed something'),
                          ('Anlamadım, tekrar eder misiniz?', 'the answer was not a clear yes')):
            api, sim, bank = call(self.turkish())
            sim.hear('Yarın ödeyebilirim.')
            check(api, sim)
            sim.say(READ_BACK_TR)
            sim.hear(said)
            self.assertIn(why, refusal(sim, create(api, sim)), said)
            self.assertEqual(bank.ledger['promises'], [], said)

    def test_a_read_back_without_the_date_is_no_read_back_in_turkish_either(self):
        api, sim, bank = call(self.turkish())
        sim.hear('Yarın ödeyebilirim.')
        check(api, sim)
        sim.say('500 TL yarın için kaydediyorum, onaylıyor musunuz?')
        sim.hear('Evet.')
        self.assertIn('you did not read back', refusal(sim, create(api, sim)))
        self.assertEqual(bank.ledger['promises'], [])


class TheInputsOfARun(unittest.TestCase):
    def test_a_file_is_read_with_or_without_a_byte_order_mark_and_in_the_turkish_code_page(self):
        text, lines = 'Müşteri İĞNE şğüöçı, tutar 1.500,00 TL', []
        with tempfile.TemporaryDirectory() as tmp:
            for name, raw in (('plain.md', text.encode('utf-8')), ('bom.md', b'\xef\xbb\xbf' + text.encode('utf-8')), ('ansi.md', text.encode('cp1254'))):
                (Path(tmp) / name).write_bytes(raw)
                self.assertEqual(builder_run.text_of(Path(tmp) / name, lines.append), text, name)
            self.assertEqual(len(lines), 1, 'only the file that is not UTF-8 is remarked on')
            self.assertIn('ansi.md is not UTF-8; read as Windows-1254 (Turkish)', lines[0])
            (Path(tmp) / 'api.json').write_bytes(b'\xef\xbb\xbf' + json.dumps({'a': 'ş'}).encode('utf-8'))
            self.assertEqual(json.loads(builder_run.text_of(Path(tmp) / 'api.json')), {'a': 'ş'})

    def build(self, tmp, *argv):
        spec = Path(tmp) / 'spec.json'
        spec.write_text(json.dumps(fixture(True)), encoding='utf-8')
        with mock.patch.object(settings, 'load_env'), mock.patch('builtins.print'):
            builder_run.main([str(KIT / 'briefs' / 'brief-a.md'), str(KIT / 'tools' / 'openapi.json'), '--spec', str(spec), '--no-deploy', '--slot', 'final', '--out', str(Path(tmp) / 'out'),
                              '--trace', str(Path(tmp) / 'final.log')] + list(argv), registry=Path(tmp) / 'agents.json')
        trace = sorted(Path(tmp).glob('final*.log'), key=lambda p: p.stat().st_mtime_ns)[-1].read_text(encoding='utf-8')
        return (Path(tmp) / 'out' / 'final' / 'workflow.json').read_text(encoding='utf-8'), trace

    def test_the_bank_of_a_run_can_be_at_another_address_than_the_tool_api_file_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            usual, trace = self.build(tmp)
            self.assertNotIn('--mock-url', trace, 'without the flag nothing changes')
            own = json.loads((KIT / 'tools' / 'openapi.json').read_text(encoding='utf-8'))['servers'][0]['url'].rstrip('/')
            self.assertIn(own + '/', usual)
            moved, trace = self.build(tmp, '--mock-url', 'https://mock.example.test/final/')
            self.assertIn('| inputs | --mock-url: the tools are compiled for https://mock.example.test/final (the tool API file names %s)' % own, trace)
            self.assertIn('https://mock.example.test/final/', moved)
            self.assertNotIn(own, moved, 'no tool is left at the address of the file')
            self.assertEqual(moved.replace('https://mock.example.test/final', own), usual, 'and nothing else differs')
            _, trace = self.build(tmp, '--mock-url', 'http://mock.example.test')
            self.assertIn('SPEC ERROR | tool API has no https server url', trace, 'an address the platform cannot call is refused as it is in a file')


if __name__ == '__main__':
    unittest.main()
