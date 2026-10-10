"""The bank's tool API, read straight from the OpenAPI document."""
from builder.spec.models import TOOLS


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
        for _ in range(20):
            if isinstance(schema, dict) and '$ref' in schema:
                node = self.document
                for part in schema['$ref'].lstrip('#/').split('/'):
                    node = node.get(part, {})
                schema = node
            else:
                break
        return schema if isinstance(schema, dict) else {}

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

    def product_schema(self, path):
        schema = self.response('get_account')
        for part in [p for p in path.split('.') if p]:
            schema = self._resolve((schema.get('properties') or {}).get(part) or {})
        return {k: self._resolve(v) for k, v in (self._resolve(schema.get('items') or {}).get('properties') or {}).items()}
