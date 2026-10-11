"""The bank's tool API, read straight from the OpenAPI document."""
import re

from builder.spec.models import TOOLS

METHODS = ('post', 'put', 'patch', 'get', 'delete')
CREDENTIALS = ('authorization', 'proxyauthorization', 'cookie', 'apikey', 'xapikey', 'accesstoken', 'token', 'password', 'secret')       # names of values no build carries


def resolve(document, schema):
    """The schema a `$ref` points to, followed through the document; {} for a reference that leads nowhere."""
    for _ in range(20):
        if not (isinstance(schema, dict) and '$ref' in schema):
            break
        node = document
        for part in str(schema['$ref']).lstrip('#/').split('/'):
            node = node.get(part, {}) if isinstance(node, dict) else {}
        schema = node
    return schema if isinstance(schema, dict) else {}


class Api:
    """Operations by tool name, with their request fields, response fields and URLs."""

    def __init__(self, document):
        self.document = document
        servers = document.get('servers') or []
        self.base = str(servers[0].get('url', '')).rstrip('/') if servers else ''
        self.ops = {}
        for path, item in (document.get('paths') or {}).items():
            for method, op in (item or {}).items():
                if method.lower() not in ('post', 'put', 'patch', 'get', 'delete') or not isinstance(op, dict):
                    continue
                name = op.get('operationId') if op.get('operationId') in TOOLS else path.strip('/').split('/')[-1]
                self.ops[name] = {'path': path, 'method': method.upper(), 'op': op}
        # A document with the seven operations of a collections call is read as it always was. Any other document
        # is the tool API of a general agent: every operation gets a name a tool can carry, and that name is
        # written into the document as its operationId, so that the extraction is given the names it has to use.
        self.legacy = all(tool in self.ops for tool in TOOLS)
        if not self.legacy:
            self.ops = {}
            for path, item in (document.get('paths') or {}).items():
                for method, op in (item or {}).items():
                    if method.lower() in METHODS and isinstance(op, dict):
                        name = tool_name(op.get('operationId'), method, path, self.ops)
                        op['operationId'] = name
                        self.ops[name] = {'path': path, 'method': method.upper(), 'op': op}

    def fields(self, tool):
        """Every value a request of this operation carries, by name: where it goes ('body', 'path', 'query',
        'header'), its schema and whether it is required.

        A field inside an object of the request body is a field of its own, with `path`, the names from the body
        down to it. It is named by its own name when no other field of the request has that name, else by its
        path joined with underscores. An object without declared fields, and a list, stay one field."""
        entry, out = self.ops[tool], {}
        item = (self.document.get('paths') or {}).get(entry['path']) or {}
        for param in (entry['op'].get('parameters') or []) + (item.get('parameters') or []):
            param = self._resolve(param)
            if param.get('name') and param['name'] not in out:
                schema = dict(self._resolve(param.get('schema') or {}))
                schema.setdefault('description', str(param.get('description') or ''))
                out[param['name']] = {'in': str(param.get('in') or 'query'), 'schema': schema, 'required': bool(param.get('required')) or param.get('in') == 'path',
                                      'credential': re.sub(r'[^a-z]', '', str(param['name']).lower()) in CREDENTIALS}
        for name in re.findall(r'\{([^{}/]+)\}', entry['path']):
            out.setdefault(name, {'in': 'path', 'schema': {'type': 'string'}, 'required': True})
        props, required = self.body(tool)
        leaves = list(self._leaves(props, required, [], True))
        names = [path[-1] for path, _, _ in leaves]
        for path, schema, needed in leaves:
            name = path[-1] if names.count(path[-1]) == 1 and path[-1] not in out else '_'.join(path)
            out.setdefault(name, {'in': 'body', 'schema': schema, 'required': needed, 'path': path})
        return out

    def _leaves(self, props, required, above, needed):
        for name, schema in props.items():
            schema = self._resolve(schema)
            inner = {k: self._resolve(v) for k, v in (schema.get('properties') or {}).items()}
            if inner and schema.get('type', 'object') == 'object' and len(above) < 5:
                for leaf in self._leaves(inner, list(schema.get('required') or []), above + [name], needed and name in required):
                    yield leaf
            else:
                yield above + [name], schema, needed and name in required

    def statuses(self, tool):
        """The answers the document lists for this operation: {status: description}."""
        return {str(status): str((self._resolve(resp) or {}).get('description') or '') for status, resp in (self.ops[tool]['op'].get('responses') or {}).items()}

    def _resolve(self, schema):
        return resolve(self.document, schema)

    def url(self, tool):
        return self.base + self.ops[tool]['path']

    def summary(self, tool):
        op = self.ops[tool]['op']
        return str(op.get('summary') or op.get('description') or '').strip()

    def body(self, tool):
        content = ((self.ops[tool]['op'].get('requestBody') or {}).get('content') or {})
        schema = self._resolve((content.get('application/json') or {}).get('schema') or {})
        props = {k: self._resolve(v) for k, v in (schema.get('properties') or {}).items()}
        return props, list(schema.get('required') or [])

    def response(self, tool):
        for status, resp in (self.ops[tool]['op'].get('responses') or {}).items():
            if str(status).startswith('2'):
                content = (resp or {}).get('content') or {}
                return self._resolve((content.get('application/json') or {}).get('schema') or {})
        return {}

    def success_flags(self):
        """The true/false fields that the 2xx answer of every operation carries. One of them says whether the bank did it."""
        common = None
        for tool in self.ops:
            props = self.response(tool).get('properties') or {}
            names = {k for k, v in props.items() if self._resolve(v).get('type') == 'boolean'}
            common = names if common is None else common & names
        return sorted(common or [])

    def outside_body(self, tool):
        """What a request of this operation carries outside its JSON body. A pre-hook can only rewrite the body."""
        entry = self.ops[tool]
        found = []
        item = (self.document.get('paths') or {}).get(entry['path']) or {}
        for param in (entry['op'].get('parameters') or []) + (item.get('parameters') or []):
            param = self._resolve(param)
            found.append('%s parameter %s' % (param.get('in'), param.get('name')))
        if '{' in entry['path']:
            found.append('a path template')
        content = (entry['op'].get('requestBody') or {}).get('content') or {}
        if content and 'application/json' not in content:
            found.append('a body that is not JSON')
        return found

    def product_schema(self, path):
        schema = self.response('get_account')
        for part in [p for p in path.split('.') if p]:
            schema = self._resolve((schema.get('properties') or {}).get(part) or {})
        return {k: self._resolve(v) for k, v in (self._resolve(schema.get('items') or {}).get('properties') or {}).items()}


def tool_name(operation_id, method, path, taken=()):
    """The name of an operation as a tool: its operationId, else the last part of its path, else method and
    path; only letters, digits and underscores, and never a name another operation already has."""
    last = path.strip('/').split('/')[-1]
    for name in (re.sub(r'[^A-Za-z0-9_]+', '_', str(operation_id or '')).strip('_'), last if re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', last) else '',
                 re.sub(r'[^A-Za-z0-9]+', '_', method.lower() + '_' + path).strip('_')):
        if name and name[0].isalpha() and name not in taken:
            return name[:60]
    return re.sub(r'[^A-Za-z0-9]+', '_', 'op_%d_%s_%s' % (len(taken) + 1, method.lower(), path)).strip('_')[:60]


def _names(document, schema, depth=0, seen=None):
    """The property names of a schema and of the objects and lists inside it."""
    schema = resolve(document, schema)
    seen = seen if seen is not None else set()
    if depth > 6 or not isinstance(schema, dict):
        return seen
    for name, child in (schema.get('properties') or {}).items():
        seen.add(str(name))
        _names(document, child, depth + 1, seen)
    for key in ('items', 'additionalProperties'):
        if isinstance(schema.get(key), dict):
            _names(document, schema[key], depth + 1, seen)
    for key in ('allOf', 'oneOf', 'anyOf'):
        for part in schema.get(key) or []:
            _names(document, part, depth + 1, seen)
    return seen


def evidence(document, texts):
    """Where the documents state what a tool API holds that was written from them: (rows, problems).

    `texts` is [(name, text)]. Every path, every method, every name of a request or response field and every
    status of every operation has to stand in the documents as it is written in the tool API. A row says where
    it was found: {'operation', 'what', 'value', 'document', 'line', 'text'}. What is found nowhere is a
    problem and no row: a tool API is not built on a name or a status that no document states. A path is
    looked for as written, with any name in its braces. Documents also give paths as one address and the names
    of the services under it: a path whose last part stands in a document as a name of its own, and whose part
    before it stands in a document as part of an address, is found that way, and its row says so. A method is
    looked for on a line that also names the path or its last fixed part, and failing that anywhere in a
    document that names the path or the service."""
    lines = [(name, number, line) for name, text in texts for number, line in enumerate(str(text).splitlines(), 1)]

    def find(pattern, flags=0, also=None):
        for name, number, line in lines:
            if re.search(pattern, line, flags) and (also is None or re.search(also, line, flags)):
                return {'document': name, 'line': number, 'text': line.strip()[:200]}
        return None

    def word(token):
        return r'(?<![A-Za-z0-9_])' + re.escape(str(token)) + r'(?![A-Za-z0-9_])'

    rows, problems = [], []

    def need(title, what, value, found):
        if found:
            rows.append(dict({'operation': title, 'what': what, 'value': str(value)}, **found))
        else:
            problems.append('%s: the %s %r stands in no document' % (title, what, str(value)))
        return found

    for path, item in (document.get('paths') or {}).items():
        for method, op in (item or {}).items():
            if method.lower() not in METHODS or not isinstance(op, dict):
                continue
            title = '%s %s' % (method.upper(), path)
            shape = ''.join(r'[{<:]?[A-Za-z0-9_]+[}>]?' if part.startswith('{') else re.escape(part) for part in re.split(r'(\{[^{}/]+\})', path))
            fixed = [part for part in path.strip('/').split('/') if part and not part.startswith('{')]
            stated = find(shape)
            if not stated and fixed and not path.rstrip('/').endswith('}'):
                before = path.rstrip('/').rsplit('/', 1)[0]
                named = find(word(fixed[-1]))
                if named and (not before or find(re.escape(before) + r'(?![A-Za-z0-9_])')):
                    stated = dict(named, text='(the last part as a name of its own, the part before it as part of an address) ' + named['text'])
            stated = need(title, 'path', path, stated)
            near = find(word(method), re.I, shape) or (find(word(method), re.I, re.escape(fixed[-1])) if fixed else None)
            if not near and stated:
                # Any document that names the path or the service, not only the first one that does: a service is
                # often mentioned in passing before the document that describes it.
                holders = {name for name, _, line in lines if re.search(shape, line) or (fixed and re.search(word(fixed[-1]), line))}
                near = next(({'document': name, 'line': number, 'text': line.strip()[:200]} for name, number, line in lines
                             if name in holders and re.search(word(method), line, re.I)), None)
            need(title, 'method', method.upper(), near)
            requests = set()
            for param in (op.get('parameters') or []) + ((item or {}).get('parameters') or []):
                requests.add(str(resolve(document, param).get('name') or ''))
            for content in ((op.get('requestBody') or {}).get('content') or {}).values():
                _names(document, (content or {}).get('schema') or {}, 0, requests)
            for name in sorted(n for n in requests if n):
                need(title, 'request field', name, find(word(name)))
            answers = set()
            for status, resp in (op.get('responses') or {}).items():
                if str(status) != 'default':
                    need(title, 'status', status, find(r'(?<![0-9.,])' + re.escape(str(status)) + r'(?![0-9])'))
                for content in ((resolve(document, resp) or {}).get('content') or {}).values():
                    _names(document, (content or {}).get('schema') or {}, 0, answers)
            for name in sorted(answers):
                need(title, 'response field', name, find(word(name)))
    if not rows and not problems:
        problems.append('the tool API has no operation')
    return rows, problems
