"""The documents of a case, as text: Markdown, plain text, Word (.docx), Excel (.xlsx), PDF, and the text formats
JSON, YAML, CSV, XML, mail (.eml) and HTML.

    read(path)      one file -> (text, warnings)
    folder(path)    a case folder -> the documents in a fixed order, the tool API file if there is exactly
                    one, what was skipped, and every warning

Nothing here summarises, translates, reorders or decides: the text of a file is what the file holds, in the
order it holds it, with a marker in square brackets where a table, a sheet or a page begins. Two documents that
contradict each other are both handed on as they are; which of them holds is for the extraction to read from
the documents.

Word and Excel files are read with the standard library (they are zip files of XML). A formula of a sheet is
written as its formula and the value the file stores for it; nothing is computed. PDF needs `pypdf`; a PDF
without a text layer (a scan) gives a warning and no text, and there is no OCR.

JSON, YAML, CSV and XML are handed on as the text they are. A mail is its date, sender, recipients and subject
and then its text; its attachments are named, and read only where they lie as files of their own. An HTML page
is its text without markup, scripts and styles.

A JSON file of data that is longer than MOST characters is not handed on whole: of every list in it, the entries
that show a shape no earlier entry of that list showed are kept (at most a few), and a line says how many were
left out. A warning says so. Every field name of the file is still there; most of its values are not.
"""
import datetime
import email
import email.policy
import html.parser
import json
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree

TEXT, WORD, EXCEL, PDF = ('.md', '.markdown', '.txt', '.json', '.yaml', '.yml', '.csv', '.tsv', '.xml'), ('.docx',), ('.xlsx',), ('.pdf',)
MAIL, HTML = ('.eml',), ('.html', '.htm')
SUPPORTED = TEXT + WORD + EXCEL + PDF + MAIL + HTML
MOST = 40000                    # characters of a JSON data file that are handed on whole
W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
S = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
R = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
DATE_FORMATS = set(range(14, 23)) | set(range(45, 48))          # the built-in number formats of Excel that show a date or a time


def decode(raw):
    """(text, warning or ''): UTF-8 with or without a byte order mark; otherwise Windows-1254, and a warning."""
    try:
        return raw.decode('utf-8-sig'), ''
    except UnicodeDecodeError:
        return raw.decode('cp1254', errors='replace'), 'not UTF-8; read as Windows-1254 (Turkish). Check its special characters'


def _word_text(node):
    """The text of a paragraph or a cell, with its tabs and line breaks."""
    parts = []
    for child in node.iter():
        if child.tag == W + 't':
            parts.append(child.text or '')
        elif child.tag == W + 'tab':
            parts.append('\t')
        elif child.tag in (W + 'br', W + 'cr'):
            parts.append('\n')
    return ''.join(parts)


def read_docx(path):
    """Paragraphs and tables in the order of the document. A table is one line per row, its cells apart by ' | '."""
    with zipfile.ZipFile(path) as archive:
        body = ElementTree.fromstring(archive.read('word/document.xml')).find(W + 'body')
    lines, tables = [], 0
    for block in list(body) if body is not None else []:
        if block.tag == W + 'p':
            lines.append(_word_text(block))
        elif block.tag == W + 'tbl':
            tables += 1
            lines.append('[table %d]' % tables)
            for number, row in enumerate(block.findall(W + 'tr'), 1):
                cells = [' '.join(_word_text(p) for p in cell.iter(W + 'p')).strip() for cell in row.findall(W + 'tc')]
                lines.append('row %d: %s' % (number, ' | '.join(cells)))
            lines.append('[end of table %d]' % tables)
    return '\n'.join(lines), []


def _column(ref):
    return re.sub(r'[0-9]', '', ref or '')


def _excel_date(serial):
    """A stored day number as a date (and a time, if it has one), or None."""
    try:
        moment = datetime.datetime(1899, 12, 30) + datetime.timedelta(days=float(serial))
    except (ValueError, OverflowError):
        return None
    return moment.date().isoformat() if float(serial) == int(float(serial)) else moment.isoformat(sep=' ', timespec='seconds')


def read_xlsx(path):
    """Every sheet with its name; every row with its number; every cell with its place and, from the second row
    of a sheet on, the heading of its column. A formula is written as `=formula (stored value: v)`."""
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        strings = []
        if 'xl/sharedStrings.xml' in names:
            strings = [''.join(t.text or '' for t in item.iter(S + 't')) for item in ElementTree.fromstring(archive.read('xl/sharedStrings.xml')).findall(S + 'si')]
        dates = []                                                  # per cell style: does it show a date
        if 'xl/styles.xml' in names:
            styles = ElementTree.fromstring(archive.read('xl/styles.xml'))
            custom = {int(f.get('numFmtId')): f.get('formatCode') or '' for f in styles.iter(S + 'numFmt')}
            formats = styles.find(S + 'cellXfs')
            for xf in list(formats) if formats is not None else []:
                number = int(xf.get('numFmtId') or 0)
                code = re.sub(r'"[^"]*"|\[[^\]]*\]', '', custom.get(number, ''))
                dates.append(number in DATE_FORMATS or bool(re.search(r'[dyDY]|mm', code)))
        targets = {rel.get('Id'): rel.get('Target') for rel in ElementTree.fromstring(archive.read('xl/_rels/workbook.xml.rels'))}
        lines = []
        for sheet in ElementTree.fromstring(archive.read('xl/workbook.xml')).iter(S + 'sheet'):
            target = targets.get(sheet.get(R + 'id')) or ''
            member = target.lstrip('/') if target.startswith('/') else 'xl/' + target
            lines.append('[sheet "%s"]' % sheet.get('name'))
            headers, first = {}, True
            for row in ElementTree.fromstring(archive.read(member)).iter(S + 'row'):
                cells = []
                for cell in row.findall(S + 'c'):
                    kind, value, formula = cell.get('t'), cell.find(S + 'v'), cell.find(S + 'f')
                    text = value.text if value is not None and value.text is not None else ''
                    if kind == 's' and text.isdigit() and int(text) < len(strings):
                        text = strings[int(text)]
                    elif kind == 'inlineStr':
                        text = ''.join(t.text or '' for t in cell.iter(S + 't'))
                    elif kind == 'b':
                        text = 'TRUE' if text == '1' else 'FALSE'
                    elif kind is None and text and (cell.get('s') or '').isdigit() and int(cell.get('s')) < len(dates) and dates[int(cell.get('s'))]:
                        shown = _excel_date(text)
                        text = '%s (stored as %s)' % (shown, text) if shown else text
                    if formula is not None:
                        text = '=%s (stored value: %s)' % (formula.text or '[shared formula]', text if text != '' else 'none')
                    if text != '':
                        cells.append((_column(cell.get('r')), cell.get('r') or '?', text))
                if not cells:
                    continue
                if first:
                    headers, first = {column: text for column, _, text in cells}, False
                    lines.append('row %s (first row): %s' % (row.get('r') or '?', ' | '.join('%s = %s' % (ref, text) for _, ref, text in cells)))
                else:
                    lines.append('row %s: %s' % (row.get('r') or '?', ' | '.join('%s [%s] = %s' % (headers.get(column, 'no heading'), ref, text) for column, ref, text in cells)))
            lines.append('[end of sheet "%s"]' % sheet.get('name'))
    return '\n'.join(lines), []


def read_pdf(path):
    """The text of every page under its page number. A page without text is said to be one."""
    try:
        import pypdf
    except ImportError:
        return '', ['PDF cannot be read: the pypdf package is not installed (pip install pypdf). Its content is NOT part of this run']
    reader = pypdf.PdfReader(str(path))
    lines, empty = [], []
    for number, page in enumerate(reader.pages, 1):
        text = (page.extract_text() or '').strip()
        lines += ['[page %d]' % number, text if text else '[this page has no text layer]']
        if not text:
            empty.append(number)
    if len(empty) == len(reader.pages):
        return '', ['no page of this PDF has a text layer (a scan or images): nothing could be read and no OCR is made. Its content is NOT part of this run']
    return '\n'.join(lines), (['page(s) %s have no text layer (images?): their content is NOT part of this run' % ', '.join(map(str, empty))] if empty else [])


class _Page(html.parser.HTMLParser):
    """The text of an HTML page: what stands between the tags, a new line where a block ends, nothing of a
    script or a style."""
    BLOCKS = ('p', 'div', 'br', 'tr', 'li', 'table', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'section', 'article', 'pre', 'blockquote')

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.hidden = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.hidden += 1
        elif tag in self.BLOCKS:
            self.parts.append('\n')
        elif tag in ('td', 'th'):
            self.parts.append(' | ')

    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self.hidden = max(0, self.hidden - 1)
        elif tag in self.BLOCKS:
            self.parts.append('\n')

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def html_text(markup):
    page = _Page()
    page.feed(markup)
    page.close()
    lines = [re.sub(r'[ \t\xa0]+', ' ', line).strip() for line in ''.join(page.parts).splitlines()]
    return '\n'.join(line for line, before in zip(lines, [''] + lines) if line or before)


def read_html(path):
    text, warning = decode(Path(path).read_bytes())
    return html_text(text), [warning] if warning else []


def read_eml(path):
    """A mail: its date, sender, recipients and subject, then its text (the plain part, else the HTML part as
    text). An attachment is named and not read here."""
    message = email.message_from_bytes(Path(path).read_bytes(), policy=email.policy.default)
    lines = ['[mail] %s: %s' % (name, message[name]) for name in ('Date', 'From', 'To', 'Cc', 'Subject') if message[name]]
    body = message.get_body(preferencelist=('plain', 'html'))
    warnings = []
    if body is None:
        warnings.append('the mail has no text part')
    else:
        try:
            content = body.get_content()
        except (LookupError, UnicodeError):
            content, warning = decode(body.get_payload(decode=True) or b'')
            warnings += [warning] if warning else []
        lines += ['', html_text(content) if body.get_content_type() == 'text/html' else str(content)]
    attached = [part.get_filename() for part in message.iter_attachments() if part.get_filename()]
    if attached:
        lines += ['', '[attachments of this mail, read only where they are files of the case: %s]' % ', '.join(attached)]
    return '\n'.join(lines), warnings


def _shape(value, prefix='', depth=0):
    """The paths of the names in a value: what two entries of a list have in common when they are of one kind."""
    found = set()
    if isinstance(value, dict) and depth < 8:
        for key, child in value.items():
            found |= _shape(child, prefix + '.' + str(key), depth + 1)
    elif isinstance(value, list) and depth < 8:
        for child in value:
            found |= _shape(child, prefix + '[]', depth + 1)
    return found or {prefix + ':' + type(value).__name__}


def sample(value, keep):
    """A value with every list in it shortened to the entries that show a new shape, at most `keep` of them."""
    if isinstance(value, dict):
        return {key: sample(child, keep) for key, child in value.items()}
    if isinstance(value, list):
        kept, seen = [], set()
        for child in value:
            shape = frozenset(_shape(child))
            if shape not in seen and len(kept) < keep:
                seen.add(shape)
                kept.append(sample(child, keep))
        if len(kept) < len(value):
            kept.append('[%d more entries of this list are left out here]' % (len(value) - len(kept)))
        return kept
    return value


def read_data(path):
    """A JSON file as text: as it is, or, when it is longer than MOST characters, as a sample of its lists."""
    text, warning = decode(Path(path).read_bytes())
    warnings = [warning] if warning else []
    if len(text) <= MOST:
        return text, warnings
    try:
        data = json.loads(text)
    except ValueError:
        return text[:MOST], warnings + ['%d characters that are no valid JSON: only the first %d are part of this run' % (len(text), MOST)]
    for keep in (6, 3, 2, 1):
        short = json.dumps(sample(data, keep), ensure_ascii=False, indent=1)
        if len(short) <= 2 * MOST:
            break
    return short, warnings + ['%d characters of data: of every list only the entries that show a new shape are part of this run, at most %d per list (%d characters). '
                              'Every field name is kept; the values of the entries left out are NOT part of this run' % (len(text), keep, len(short))]


def read(path):
    """(text, warnings) of one file, by its ending. Raises ValueError for an ending that is not read, and
    whatever the file's format raises when the file is not what its ending says."""
    path = Path(path)
    kind = path.suffix.lower()
    if kind == '.json':
        return read_data(path)
    if kind in TEXT:
        text, warning = decode(path.read_bytes())
        return text, [warning] if warning else []
    if kind in WORD:
        return read_docx(path)
    if kind in EXCEL:
        return read_xlsx(path)
    if kind in PDF:
        return read_pdf(path)
    if kind in MAIL:
        return read_eml(path)
    if kind in HTML:
        return read_html(path)
    raise ValueError('files ending in %s are not read' % (kind or 'nothing'))


def is_tool_api(path):
    """Whether a JSON file is an OpenAPI document: an object with `openapi` or `swagger`, and `paths`."""
    try:
        document = json.loads(decode(Path(path).read_bytes())[0])
    except (ValueError, OSError):
        return False
    return isinstance(document, dict) and ('openapi' in document or 'swagger' in document) and isinstance(document.get('paths'), dict)


def folder(path):
    """The case in a folder: {'documents': [(name, text, file)], 'tool_apis': [files], 'warnings': [...], 'skipped': [...]}.

    Every file under the folder is looked at, in the order of its path relative to the folder (compared without
    regard to case, then exactly), so that the same folder gives the same documents in the same order on every
    machine. A document's name is that relative path. A file that cannot be read, or holds no text, is not a
    document: it is a warning, never a silent gap. A JSON file that is an OpenAPI document is a tool API and no
    document; a JSON file that is none is a document like any other text. A file of another format is skipped and listed."""
    root = Path(path)
    found = {'documents': [], 'tool_apis': [], 'warnings': [], 'skipped': []}
    files = sorted((p for p in root.rglob('*') if p.is_file()), key=lambda p: (p.relative_to(root).as_posix().casefold(), p.relative_to(root).as_posix()))
    for file in files:
        name = file.relative_to(root).as_posix()
        kind = file.suffix.lower()
        if kind == '.json' and is_tool_api(file):
            found['tool_apis'].append(file)
        elif kind not in SUPPORTED:
            found['skipped'].append(name)
            if kind in ('.doc', '.xls', '.ppt', '.pptx', '.msg', '.rtf', '.odt', '.png', '.jpg', '.jpeg', '.tif', '.tiff'):
                found['warnings'].append('%s: files ending in %s are not read. If it holds rules or the tool API, they are NOT part of this run' % (name, kind))
        else:
            try:
                text, warnings = read(file)
            except Exception as exc:
                found['warnings'].append('%s: cannot be read as %s (%s: %s). Its content is NOT part of this run' % (name, kind, type(exc).__name__, str(exc)[:200]))
                continue
            found['warnings'] += ['%s: %s' % (name, warning) for warning in warnings]
            if text.strip():
                found['documents'].append((name, text, file))
            elif not warnings:
                found['warnings'].append('%s: holds no text' % name)
    return found
