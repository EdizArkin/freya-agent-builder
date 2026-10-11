"""The bank's tool API, read straight from the OpenAPI document."""
from builder.spec.models import TOOLS


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
