"""The documents of a case: Markdown, text, Word, Excel and PDF files, read as they are, and a case folder as
the first argument of the command.

Offline, with small files written here: Word and Excel files as the zip of XML that they are, a PDF written
byte by byte. They are SYNTHETIC and show that what a file holds comes out unchanged, in order and under its
source. They say nothing about the files of a real case, whose layout is not known here.
"""
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from benchmarks.harness.fixture_spec import spec as fixture
from builder import run as builder_run
from builder.config import settings
from builder.extraction import documents
from tests.support import KIT

TURKISH = 'Müşteri İĞNE şğüöçı: ödeme sözü en az %40, tutar 1.500,00 TL, son tarih 15.10.2026, kimlik TR-0042.'
W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def docx(path, body):
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('[Content_Types].xml', '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        archive.writestr('word/document.xml', '<?xml version="1.0" encoding="UTF-8"?><w:document %s><w:body>%s</w:body></w:document>' % (W, body))


def para(*runs):
    return '<w:p>%s</w:p>' % ''.join('<w:r><w:t xml:space="preserve">%s</w:t></w:r>' % run for run in runs)


def table(*rows):
    return '<w:tbl>%s</w:tbl>' % ''.join('<w:tr>%s</w:tr>' % ''.join('<w:tc>%s</w:tc>' % para(cell) for cell in row) for row in rows)


def xlsx(path, sheets, strings=(), styles=''):
    """`sheets` is [(name, rows xml)]."""
    main = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('xl/workbook.xml', '<workbook %s xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>%s</sheets></workbook>' % (
            main, ''.join('<sheet name="%s" sheetId="%d" r:id="rId%d"/>' % (name, i, i) for i, (name, _) in enumerate(sheets, 1))))
        archive.writestr('xl/_rels/workbook.xml.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">%s</Relationships>' % ''.join(
            '<Relationship Id="rId%d" Type="t" Target="worksheets/sheet%d.xml"/>' % (i, i) for i in range(1, len(sheets) + 1)))
        archive.writestr('xl/sharedStrings.xml', '<sst %s>%s</sst>' % (main, ''.join('<si><t>%s</t></si>' % s for s in strings)))
        if styles:
            archive.writestr('xl/styles.xml', '<styleSheet %s>%s</styleSheet>' % (main, styles))
        for i, (_, rows) in enumerate(sheets, 1):
            archive.writestr('xl/worksheets/sheet%d.xml' % i, '<worksheet %s><sheetData>%s</sheetData></worksheet>' % (main, rows))


def pdf(path, pages):
    """A PDF with one line of text per page, in the Latin code page of the standard fonts ('' is a page without text)."""
    objects = ['<< /Type /Catalog /Pages 2 0 R >>', None, '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>']
    kids = []
    for text in pages:
        escaped = text.encode('cp1252').replace(b'\\', b'\\\\').replace(b'(', b'\\(').replace(b')', b'\\)')
        stream = b'BT /F1 12 Tf 72 720 Td (' + escaped + b') Tj ET' if text else b''
        objects.append(b'<< /Length ' + str(len(stream)).encode() + b' >>\nstream\n' + stream + b'\nendstream')
        objects.append('<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents %d 0 R /Resources << /Font << /F1 3 0 R >> >> >>' % len(objects))
        kids.append('%d 0 R' % len(objects))
    objects[1] = '<< /Type /Pages /Kids [%s] /Count %d >>' % (' '.join(kids), len(kids))
    out, offsets = b'%PDF-1.4\n', []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += str(number).encode() + b' 0 obj\n' + (body if isinstance(body, bytes) else body.encode('latin-1')) + b'\nendobj\n'
    xref = len(out)
    out += b'xref\n0 ' + str(len(objects) + 1).encode() + b'\n0000000000 65535 f \n' + b''.join(('%010d 00000 n \n' % o).encode() for o in offsets)
    out += b'trailer\n<< /Size ' + str(len(objects) + 1).encode() + b' /Root 1 0 R >>\nstartxref\n' + str(xref).encode() + b'\n%%EOF\n'
    Path(path).write_bytes(out)


class OneFile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_markdown_and_text_keep_every_character_with_or_without_a_byte_order_mark(self):
        for name, raw in (('a.md', TURKISH.encode('utf-8')), ('b.txt', b'\xef\xbb\xbf' + TURKISH.encode('utf-8')), ('c.MD', ('| Ürün | Tutar |\n|---|---|\n| Kart | ₺1.500,00 |\n' + TURKISH).encode('utf-8'))):
            (self.root / name).write_bytes(raw)
            text, warnings = documents.read(self.root / name)
            self.assertEqual((text.endswith(TURKISH), warnings, text.startswith('﻿')), (True, [], False), name)
        self.assertIn('| Kart | ₺1.500,00 |', documents.read(self.root / 'c.MD')[0], 'a Markdown table stays the table it is')
        (self.root / 'ansi.txt').write_bytes(TURKISH.encode('cp1254'))
        text, warnings = documents.read(self.root / 'ansi.txt')
        self.assertEqual(text, TURKISH)
        self.assertIn('not UTF-8; read as Windows-1254', warnings[0])

    def test_a_word_file_gives_its_paragraphs_and_tables_in_the_order_of_the_document(self):
        docx(self.root / 'kurallar.docx', para('Madde 1. ', TURKISH) + table(('Ürün', 'Asgari tutar', 'Son tarih'), ('Kredi kartı', '%30', '7 iş günü'), ('İhtiyaç kredisi', '1.500,00 TL', '')) +
             para('Madde 2. Bu madde yukarıdaki tabloyu değiştirmez.') + table(('Kod', 'Anlam'), ('TR-0042', 'itiraz')))
        text, warnings = documents.read(self.root / 'kurallar.docx')
        self.assertEqual(warnings, [])
        self.assertEqual(text.splitlines(), ['Madde 1. ' + TURKISH, '[table 1]', 'row 1: Ürün | Asgari tutar | Son tarih', 'row 2: Kredi kartı | %30 | 7 iş günü', 'row 3: İhtiyaç kredisi | 1.500,00 TL | ',
                                             '[end of table 1]', 'Madde 2. Bu madde yukarıdaki tabloyu değiştirmez.', '[table 2]', 'row 1: Kod | Anlam', 'row 2: TR-0042 | itiraz', '[end of table 2]'])

    def test_an_excel_file_gives_every_sheet_row_and_cell_with_its_place_and_no_formula_is_computed_or_dropped(self):
        styles = '<numFmts count="1"><numFmt numFmtId="164" formatCode="dd.mm.yyyy"/></numFmts><cellXfs count="3"><xf numFmtId="0"/><xf numFmtId="164"/><xf numFmtId="4"/></cellXfs>'
        first = ('<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c><c r="C1" t="s"><v>2</v></c><c r="D1" t="s"><v>3</v></c></row>'
                 '<row r="2"><c r="A2" t="s"><v>4</v></c><c r="B2" s="2"><v>1500.5</v></c><c r="C2" s="1"><v>46310</v></c><c r="D2"><f>B2*0.3</f><v>450.15</v></c></row>'
                 '<row r="3"/><row r="4"><c r="A4" t="inlineStr"><is><t>İhtiyaç kredisi</t></is></c><c r="B4"><v>0.4</v></c><c r="D4"><f>SUM(B2:B4)</f></c><c r="E4" t="b"><v>1</v></c></row>')
        second = '<row r="1"><c r="A1" t="s"><v>5</v></c></row><row r="2"><c r="A2" t="str"><f>"TR-"&amp;"0042"</f><v>TR-0042</v></c></row>'
        xlsx(self.root / 'limitler.xlsx', [('Limitler', first), ('Kodlar ve İstisnalar', second)], ('Ürün', 'Tutar', 'Son tarih', 'Asgari', 'Kredi kartı', 'Kod'), styles)
        text, warnings = documents.read(self.root / 'limitler.xlsx')
        self.assertEqual(warnings, [])
        self.assertEqual(text.splitlines(), [
            '[sheet "Limitler"]', 'row 1 (first row): A1 = Ürün | B1 = Tutar | C1 = Son tarih | D1 = Asgari',
            'row 2: Ürün [A2] = Kredi kartı | Tutar [B2] = 1500.5 | Son tarih [C2] = 2026-10-15 (stored as 46310) | Asgari [D2] = =B2*0.3 (stored value: 450.15)',
            'row 4: Ürün [A4] = İhtiyaç kredisi | Tutar [B4] = 0.4 | Asgari [D4] = =SUM(B2:B4) (stored value: none) | no heading [E4] = TRUE', '[end of sheet "Limitler"]',
            '[sheet "Kodlar ve İstisnalar"]', 'row 1 (first row): A1 = Kod', 'row 2: Kod [A2] = ="TR-"&"0042" (stored value: TR-0042)', '[end of sheet "Kodlar ve İstisnalar"]'])

    def test_a_pdf_gives_its_pages_under_their_numbers_and_a_scan_gives_a_warning_and_no_text(self):
        pdf(self.root / 'iki.pdf', ['Madde 1: odeme sozu en az %40, tutar 1.500,00 TL', 'Sayfa iki: müdür önerisi, üç çözüm, 15.10.2026'])
        text, warnings = documents.read(self.root / 'iki.pdf')
        self.assertEqual((text.splitlines(), warnings), (['[page 1]', 'Madde 1: odeme sozu en az %40, tutar 1.500,00 TL', '[page 2]', 'Sayfa iki: müdür önerisi, üç çözüm, 15.10.2026'], []))
        pdf(self.root / 'tarama.pdf', ['', ''])
        text, warnings = documents.read(self.root / 'tarama.pdf')
        self.assertEqual(text, '')
        self.assertIn('no page of this PDF has a text layer (a scan or images): nothing could be read and no OCR is made', warnings[0])
        pdf(self.root / 'yarim.pdf', ['Birinci sayfa okunur.', ''])
        text, warnings = documents.read(self.root / 'yarim.pdf')
        self.assertEqual((text.splitlines()[2:], warnings), (['[page 2]', '[this page has no text layer]'], ['page(s) 2 have no text layer (images?): their content is NOT part of this run']))
        with mock.patch.dict('sys.modules', {'pypdf': None}):
            text, warnings = documents.read(self.root / 'iki.pdf')
        self.assertEqual(text, '')
        self.assertIn('the pypdf package is not installed', warnings[0])

    def test_what_is_not_what_its_name_says_is_an_error_and_never_empty_text(self):
        (self.root / 'bozuk.docx').write_bytes(b'this is no zip file')
        (self.root / 'bozuk.xlsx').write_bytes(b'PK\x03\x04 cut off')
        (self.root / 'bozuk.pdf').write_bytes(b'%PDF-1.4 and then nothing')
        (self.root / 'resim.png').write_bytes(b'\x89PNG')
        for name in ('bozuk.docx', 'bozuk.xlsx', 'bozuk.pdf', 'resim.png'):
            with self.assertRaises(Exception, msg=name):
                documents.read(self.root / name)


class ACaseFolder(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.case = Path(self.tmp.name) / 'case'
        (self.case / 'ekler' / 'Tablolar').mkdir(parents=True)
        (self.case / '01-ana-kurallar.md').write_text('Kural 1. Geri arama 10:00 ile 19:00 arasında yapılır.\n' + TURKISH, encoding='utf-8')
        (self.case / '02-degisiklik.txt').write_bytes(b'\xef\xbb\xbf' + 'Değişiklik: geri arama 10:00 ile 18:00 arasında yapılır.'.encode('utf-8'))
        docx(self.case / 'ekler' / 'B-not.docx', para('Not: itiraz eden müşteri aktarılır.'))
        xlsx(self.case / 'ekler' / 'Tablolar' / 'limit.xlsx', [('Limit', '<row r="1"><c r="A1" t="inlineStr"><is><t>Asgari</t></is></c></row><row r="2"><c r="A2"><v>40</v></c></row>')])
        pdf(self.case / 'ekler' / 'a-ek.pdf', ['Ek: tutar 1.500,00 TL'])

    def test_every_document_is_found_in_the_same_order_whole_and_under_its_path(self):
        found = documents.folder(self.case)
        self.assertEqual([name for name, _, _ in found['documents']], ['01-ana-kurallar.md', '02-degisiklik.txt', 'ekler/a-ek.pdf', 'ekler/B-not.docx', 'ekler/Tablolar/limit.xlsx'])
        self.assertEqual(documents.folder(self.case)['documents'], found['documents'], 'the same folder, the same documents')
        texts = {name: text for name, text, _ in found['documents']}
        self.assertEqual(texts['02-degisiklik.txt'], 'Değişiklik: geri arama 10:00 ile 18:00 arasında yapılır.')
        self.assertIn('19:00', texts['01-ana-kurallar.md'])
        self.assertIn('18:00', texts['02-degisiklik.txt'], 'two documents that disagree are both there as written: nothing is decided here')
        self.assertEqual((found['warnings'], found['skipped'], found['tool_apis']), ([], [], []))
        self.assertEqual([file.name for _, _, file in found['documents']][-1], 'limit.xlsx')

    def test_what_cannot_be_read_is_said_and_never_passed_over(self):
        (self.case / 'bos.md').write_text('  \n', encoding='utf-8')
        (self.case / 'bozuk.docx').write_bytes(b'no zip')
        pdf(self.case / 'tarama.pdf', [''])
        (self.case / 'eski.doc').write_bytes(b'\xd0\xcf\x11\xe0')
        (self.case / 'api.yaml').write_text('openapi: 3.0.0', encoding='utf-8')
        (self.case / 'notlar.json').write_text('{"not": "an api"}', encoding='utf-8')
        (self.case / 'tools.json').write_text(json.dumps({'openapi': '3.0.0', 'paths': {}}), encoding='utf-8')
        found = documents.folder(self.case)
        self.assertEqual(len(found['documents']), 7, 'the five that can be read, and the YAML and the JSON file, which are text')
        self.assertEqual(sorted(found['skipped']), ['eski.doc'])
        self.assertEqual({name: text for name, text, _ in found['documents']}['api.yaml'], 'openapi: 3.0.0', 'a tool API in YAML is a document: the tool API is written from it')
        self.assertEqual([file.name for file in found['tool_apis']], ['tools.json'], 'an OpenAPI document is the tool API and no brief')
        told = '\n'.join(found['warnings'])
        for part in ('bos.md: holds no text', 'bozuk.docx: cannot be read as .docx (BadZipFile', 'tarama.pdf: no page of this PDF has a text layer',
                     'eski.doc: files ending in .doc are not read. If it holds rules or the tool API, they are NOT part of this run'):
            self.assertIn(part, told)
        self.assertEqual(len(found['warnings']), 4)


class TheCommand(unittest.TestCase):
    """python3 builder/build.py {case_folder} {mock_url}, and the single brief as before. Nothing is deployed."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.case = self.root / 'Final-Case'
        self.case.mkdir()
        (self.case / '1-kurallar.md').write_text(TURKISH, encoding='utf-8')
        docx(self.case / '2-ek.docx', para('Ek kural.') + table(('Kod', 'Anlam'), ('TR-0042', 'itiraz')))
        self.api = KIT / 'tools' / 'openapi.json'
        (self.root / 'spec.json').write_text(json.dumps(fixture(True)), encoding='utf-8')

    def build(self, *argv, model=None):
        with mock.patch.object(settings, 'load_env'), mock.patch('builtins.print'):
            code = builder_run.main(list(argv) + ['--no-deploy', '--out', str(self.root / 'out'), '--trace', str(self.root / 'run.log')] + ([] if model else ['--spec', str(self.root / 'spec.json')]),
                                    client=model, registry=self.root / 'agents.json')
        trace = sorted(self.root.glob('run*.log'), key=lambda p: p.stat().st_mtime_ns)[-1].read_text(encoding='utf-8')
        return code, trace

    def test_the_arguments_of_a_case_folder_and_of_a_single_brief(self):
        args, _ = builder_run.arguments([str(self.case), 'https://mock.example.test/v1'])
        self.assertEqual((args.tool_api, args.mock_url, args.slot), (None, 'https://mock.example.test/v1', 'final-case'))
        args, _ = builder_run.arguments([str(self.case), str(self.api), '--mock-url', 'https://mock.example.test'])
        self.assertEqual((args.tool_api, args.mock_url), (str(self.api), 'https://mock.example.test'))
        args, _ = builder_run.arguments(['brief-a.md', 'openapi.json'])
        self.assertEqual((args.brief, args.tool_api, args.mock_url, args.slot), ('brief-a.md', 'openapi.json', None, 'brief-a'), 'a single brief is as it was')

    def test_a_case_folder_with_its_one_tool_api_and_the_address_of_the_bank(self):
        (self.case / 'tools.json').write_bytes(self.api.read_bytes())
        from builder.extraction import claude
        model = claude.ReplayClient([json.dumps(fixture(True)), '[]', '[]'])
        code, trace = self.build(str(self.case), 'https://mock.example.test/final', '--slot', 'final', model=model)
        self.assertIn('| run end | status incomplete |', trace, 'built and, as asked here, not deployed')
        self.assertIn('| build | wf-', trace)
        for part in ('| inputs | case folder %s | 2 document(s), in this order: 1-kurallar.md, 2-ek.docx | tool API %s' % (self.case, self.case / 'tools.json'),
                     '| inputs | --mock-url: the tools are compiled for https://mock.example.test/final', '| provenance | brief 2 of 2 | %s | sha256 ' % (self.case / '2-ek.docx'),
                     '| provenance | tool API | %s | sha256 ' % (self.case / 'tools.json'), 'The extraction reads a later document as amending an earlier one'):
            self.assertIn(part, trace)
        asked = model.seen[0][1][0]['content']
        self.assertIn('<document index="1" name="1-kurallar.md">\n' + TURKISH, asked, 'the model is given the text of the file, as it is')
        self.assertIn('<document index="2" name="2-ek.docx">\nEk kural.\n[table 1]\nrow 1: Kod | Anlam\nrow 2: TR-0042 | itiraz\n[end of table 1]', asked)
        workflow = (self.root / 'out' / 'final' / 'workflow.json').read_text(encoding='utf-8')
        self.assertIn('https://mock.example.test/final/', workflow)

    def test_a_folder_without_a_tool_api_or_without_a_document_ends_the_run_and_guesses_nothing(self):
        code, trace = self.build(str(self.case), 'https://mock.example.test')
        self.assertEqual(code, 1)
        self.assertIn('INCOMPLETE | the case holds no OpenAPI file and this run has no model to write the tool API from the documents: name the tool API file (--tool-api)', trace)
        self.assertNotIn('| build | wf-', trace)
        code, trace = self.build(str(self.case), str(self.api), '--slot', 'named')
        self.assertIn('| build | wf-', trace, 'with the tool API named, the folder is built')
        for name in ('a.json', 'b.json'):
            (self.case / name).write_bytes(self.api.read_bytes())
        code, trace = self.build(str(self.case))
        self.assertIn('holds more than one OpenAPI file', trace)
        empty = self.root / 'empty'
        empty.mkdir()
        (empty / 'foto.png').write_bytes(b'\x89PNG')
        code, trace = self.build(str(empty), str(self.api))
        self.assertIn('INCOMPLETE | the case folder %s holds no document that could be read' % empty, trace)

    def test_a_single_brief_is_read_as_before_and_may_be_a_word_file(self):
        code, trace = self.build(str(KIT / 'briefs' / 'brief-a.md'), str(self.api), '--slot', 'single')
        self.assertIn('| inputs | brief %s | tool API %s' % (KIT / 'briefs' / 'brief-a.md', self.api), trace)
        self.assertNotIn('case folder', trace)
        self.assertIn('| build | wf-', trace)
        code, trace = self.build(str(self.case / '2-ek.docx'), str(self.api), '--amend', str(self.case / '1-kurallar.md'), '--slot', 'word')
        self.assertIn('| provenance | brief 1 of 2 | %s' % (self.case / '2-ek.docx'), trace)
        self.assertIn('| build | wf-', trace)
        code, trace = self.build(str(KIT / 'briefs' / 'brief-a.md'))
        self.assertIn('INCOMPLETE | no tool API file was given', trace)


if __name__ == '__main__':
    unittest.main()
