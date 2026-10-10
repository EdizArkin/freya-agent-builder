"""Does a request, or a compiled tool, agree with the tool API document?

Only the part of JSON Schema that OpenAPI 3.0 request bodies use is implemented. A keyword
that is not implemented is reported as not checked; it is never passed silently.
"""
import datetime
import re

CHECKED = ('$ref', 'type', 'enum', 'required', 'properties', 'additionalProperties', 'format', 'minimum', 'maximum',
           'exclusiveMinimum', 'exclusiveMaximum', 'minLength', 'maxLength', 'pattern', 'items', 'minItems',
           'maxItems', 'nullable', 'allOf')
ANNOTATIONS = ('description', 'title', 'example', 'examples', 'default', 'deprecated', 'readOnly', 'writeOnly')
FORMATS = ('date', 'date-time')
TYPES = {'string': str, 'boolean': bool, 'object': dict, 'array': list}


def resolve(document, schema):
    for _ in range(20):
        if not (isinstance(schema, dict) and '$ref' in schema):
            break
        node = document
        for part in str(schema['$ref']).lstrip('#/').split('/'):
            node = node.get(part, {}) if isinstance(node, dict) else {}
        schema = node
    return schema if isinstance(schema, dict) else {}


def _is(kind, value):
    if kind == 'integer':
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == 'number':
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if kind == 'null':
        return value is None
    return isinstance(value, TYPES[kind]) if kind in TYPES else True


def check_value(document, schema, value, path='$', strict=True, notes=None):
    """Problems of `value` against `schema`. With `strict`, a field the schema does not declare is a problem."""
    notes = notes if notes is not None else []
    schema = resolve(document, schema)
    bad = []
    for key in schema:
        if key not in CHECKED and key not in ANNOTATIONS and not str(key).startswith('x-'):
            notes.append('%s: keyword %r is not checked' % (path, key))
    for part in schema.get('allOf') or []:
        bad += check_value(document, part, value, path, strict, notes)
    if value is None and schema.get('nullable'):
        return bad
    kind = schema.get('type')
    if kind and not _is(kind, value):
        return bad + ['%s: expected %s, got %s %r' % (path, kind, type(value).__name__, value)]
    if 'enum' in schema and value not in schema['enum']:
        bad.append('%s: %r is not one of %s' % (path, value, ', '.join(str(v) for v in schema['enum'])))
    if isinstance(value, str):
        if schema.get('format') == 'date':
            try:
                if len(value) != 10:
                    raise ValueError
                datetime.date.fromisoformat(value)
            except ValueError:
                bad.append('%s: %r is not a date YYYY-MM-DD' % (path, value))
        elif schema.get('format') == 'date-time':
            try:
                datetime.datetime.fromisoformat(value.replace('Z', '+00:00'))
            except ValueError:
                bad.append('%s: %r is not a date-time' % (path, value))
        elif schema.get('format'):
            notes.append('%s: format %r is not checked' % (path, schema['format']))
        if 'minLength' in schema and len(value) < schema['minLength']:
            bad.append('%s: shorter than %d' % (path, schema['minLength']))
        if 'maxLength' in schema and len(value) > schema['maxLength']:
            bad.append('%s: longer than %d' % (path, schema['maxLength']))
        if 'pattern' in schema and not re.search(schema['pattern'], value):
            bad.append('%s: %r does not match %s' % (path, value, schema['pattern']))
    if _is('number', value):
        low, high = schema.get('minimum'), schema.get('maximum')
        if low is not None and (value < low or (schema.get('exclusiveMinimum') is True and value == low)):
            bad.append('%s: %r is below the minimum %r' % (path, value, low))
        if high is not None and (value > high or (schema.get('exclusiveMaximum') is True and value == high)):
            bad.append('%s: %r is above the maximum %r' % (path, value, high))
    if isinstance(value, list):
        if 'minItems' in schema and len(value) < schema['minItems']:
            bad.append('%s: fewer than %d items' % (path, schema['minItems']))
        if 'maxItems' in schema and len(value) > schema['maxItems']:
            bad.append('%s: more than %d items' % (path, schema['maxItems']))
        for i, item in enumerate(value):
            bad += check_value(document, schema.get('items') or {}, item, '%s[%d]' % (path, i), strict, notes)
    if isinstance(value, dict):
        props = schema.get('properties') or {}
        for name in schema.get('required') or []:
            if name not in value:
                bad.append('%s: required field %s is missing' % (path, name))
        for name, item in value.items():
            if name in props:
                bad += check_value(document, props[name], item, path + '.' + name, strict, notes)
            elif props and (strict or schema.get('additionalProperties') is False):
                bad.append('%s: field %s is not in the tool API' % (path, name))
    return bad


def body_schema(api, tool):
    content = ((api.ops[tool]['op'].get('requestBody') or {}).get('content') or {})
    return resolve(api.document, (content.get('application/json') or {}).get('schema') or {})


def check_request(api, tool, request, notes=None):
    """Problems of one outgoing request {url, method, body} against the operation of `tool`."""
    if tool not in api.ops:
        return ['%s is not an operation of the tool API' % tool]
    op, bad = api.ops[tool], []
    if request.get('url') != api.base + op['path']:
        bad.append('%s: url %r is not %r' % (tool, request.get('url'), api.base + op['path']))
    if str(request.get('method') or '').upper() != op['method']:
        bad.append('%s: method %r is not %s' % (tool, request.get('method'), op['method']))
    body = request.get('body')
    if not isinstance(body, dict):
        return bad + ['%s: the body is not a JSON object' % tool]
    return bad + [tool + ' ' + p for p in check_value(api.document, body_schema(api, tool), body, 'body', True, notes)]


def check_response(api, tool, status, data, notes=None):
    """Problems of an answer of the tool API: a status the document lists, and a body of its 2xx shape."""
    responses = api.ops[tool]['op'].get('responses') or {}
    if str(status) not in responses and 'default' not in responses:
        return ['%s: status %s is not documented' % (tool, status)]
    if not str(status).startswith('2'):
        return []
    return [tool + ' ' + p for p in check_value(api.document, api.response(tool), data, 'response', False, notes)]


def check_tools(workflow, api):
    """Every compiled tool against its operation: url, method, body fields, types, enums, required."""
    bad = []
    for node in (workflow.get('nodes') or {}).values():
        for tool in (node.get('tools') or []) + (node.get('pre_actions') or []):
            fn = tool.get('function') or {}
            name = fn.get('name')
            if fn.get('handler') != '__function__:api_call':
                continue
            if name not in api.ops:
                bad.append('%s is not an operation of the tool API' % name)
                continue
            props = (fn.get('parameters') or {}).get('properties') or {}
            op = api.ops[name]
            if (props.get('url') or {}).get('default') != api.base + op['path']:
                bad.append('%s: url is not %s' % (name, api.base + op['path']))
            if str((props.get('method') or {}).get('default', '')).upper() != op['method']:
                bad.append('%s: method is not %s' % (name, op['method']))
            schema = body_schema(api, name)
            want = {k: resolve(api.document, v) for k, v in (schema.get('properties') or {}).items()}
            body = props.get('body') or {}
            have = body.get('properties') or {}
            bad += ['%s: body field %s of the tool API is missing' % (name, f) for f in want if f not in have]
            bad += ['%s: body field %s is not in the tool API' % (name, f) for f in have if f not in want]
            for field, leaf in have.items():
                if field not in want:
                    continue
                if leaf.get('type') != want[field].get('type', 'string'):
                    bad.append('%s.%s: type %s, the tool API says %s' % (name, field, leaf.get('type'), want[field].get('type', 'string')))
                if want[field].get('enum') and leaf.get('enum') and not set(leaf['enum']) <= set(want[field]['enum']):
                    bad.append('%s.%s: offers values the tool API does not allow: %s' % (
                        name, field, ', '.join(str(v) for v in leaf['enum'] if v not in want[field]['enum'])))
            required = body.get('required') or []
            bad += ['%s: %s is required of the model but not by the tool API' % (name, f) for f in required if f not in (schema.get('required') or [])]
            for field in schema.get('required') or []:
                # A field the model does not fill has to be filled from a call variable: a leaf without a description.
                if field not in required and field in have and 'description' in have[field]:
                    bad.append('%s: required field %s is left optional for the model' % (name, field))
    return list(dict.fromkeys(bad))     # a tool offered in two parts of the call is reported once


def document_notes(api):
    """Constructs of the document that the builder does not handle."""
    notes = []
    if (api.document.get('components') or {}).get('securitySchemes') or api.document.get('security'):
        notes.append('the document declares security schemes; the builder sends no credentials')
    for tool, entry in api.ops.items():
        op = entry['op']
        item = (api.document.get('paths') or {}).get(entry['path']) or {}
        for param in (op.get('parameters') or []) + (item.get('parameters') or []):
            param = resolve(api.document, param)
            notes.append('%s: %s parameter %s is not sent by the builder' % (tool, param.get('in'), param.get('name')))
        if '{' in entry['path']:
            notes.append('%s: the path %s has a template the builder does not fill' % (tool, entry['path']))
        content = (op.get('requestBody') or {}).get('content') or {}
        if content and 'application/json' not in content:
            notes.append('%s: the request body is not application/json' % tool)
        if op.get('security'):
            notes.append('%s: the operation requires credentials' % tool)
        for name, schema in (body_schema(api, tool).get('properties') or {}).items():
            schema = resolve(api.document, schema)
            if schema.get('type') in ('object', 'array'):
                notes.append('%s: body field %s is nested; the builder fills only top-level fields' % (tool, name))
    return notes
