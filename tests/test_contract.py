"""builder/validation/contract.py: requests and compiled tools against the tool API document."""
import copy
import unittest
from unittest import mock

from builder import pipeline
from builder.compiler.compiler import CompileError
from builder.pipeline import build_workflow
from builder.spec.openapi import Api
from builder.validation import contract
from tests.fixture_spec import spec as fixture
from tests.freya_sim import Bank, Sim
from tests.support import CASES, OPENAPI

API = Api(OPENAPI)
BASE = 'https://freya-bank-mock.freyavoice.workers.dev/v1/'
VALID = {
    'verify_identity': {'customer_id': 'c1', 'field': 'date_of_birth', 'answer': '1980-01-31'},
    'get_account': {'customer_id': 'c1'},
    'create_promise': {'customer_id': 'c1', 'product_id': 'p1', 'amount': 120.5, 'date': '2026-10-20'},
    'add_note': {'customer_id': 'c1', 'product_id': 'p1', 'reason': 'refused'},
    'schedule_callback': {'customer_id': 'c1', 'date': '2026-10-15', 'time': '10:00'},
    'transfer_to_agent': {'customer_id': 'c1', 'queue': 'support'},
    'close_call': {'customer_id': 'c1', 'outcome': 'promise'},
}


def request(tool, **change):
    body = dict(VALID[tool])
    for key, value in change.pop('body', {}).items():
        body.pop(key, None) if value is None else body.update({key: value})
    return dict({'url': BASE + tool, 'method': 'POST', 'body': body}, **change)


class Requests(unittest.TestCase):
    def test_a_valid_request_of_every_tool_passes(self):
        for tool in VALID:
            self.assertEqual(contract.check_request(API, tool, request(tool)), [], tool)
        self.assertEqual(contract.check_request(API, 'close_call', request('close_call', body={'reason': 'refused', 'outcome': 'no_promise'})), [])
        self.assertEqual(contract.check_request(API, 'create_promise', request('create_promise', body={'amount': 120})), [], 'an integer is a number')

    def test_each_kind_of_broken_request_is_rejected(self):
        cases = [
            ('create_promise', {'body': {'date': None}}, 'required field date is missing'),
            ('create_promise', {'body': {'amount': '120'}}, 'body.amount: expected number, got str'),
            ('create_promise', {'body': {'amount': True}}, 'body.amount: expected number, got bool'),
            ('create_promise', {'body': {'date': '20-10-2026'}}, "is not a date YYYY-MM-DD"),
            ('create_promise', {'body': {'date': '2026-10-20T09:00'}}, "is not a date YYYY-MM-DD"),
            ('create_promise', {'body': {'product_id': 7}}, 'body.product_id: expected string, got int'),
            ('add_note', {'body': {'reason': 'forgot'}}, "'forgot' is not one of refused, hardship, dispute, paid, service_error"),
            ('transfer_to_agent', {'body': {'queue': 'billing'}}, "'billing' is not one of"),
            ('close_call', {'body': {'outcome': 'done'}}, "'done' is not one of"),
            ('close_call', {'body': {'customer_id': None}}, 'required field customer_id is missing'),
            ('schedule_callback', {'body': {'note': 'call late'}}, 'field note is not in the tool API'),
            ('get_account', {'url': BASE + 'accounts'}, 'is not'),
            ('get_account', {'method': 'GET'}, "method 'GET' is not POST"),
            ('get_account', {'body': None}, 'the body is not a JSON object'),
        ]
        for tool, change, expected in cases:
            req = request(tool, **{k: v for k, v in change.items() if k != 'body' or v is not None})
            if change.get('body', {}) is None:
                req['body'] = 'customer_id=c1'
            self.assertIn(expected, ' | '.join(contract.check_request(API, tool, req)), (tool, change))
        self.assertEqual(contract.check_request(API, 'refund', request('get_account')), ['refund is not an operation of the tool API'])

    def test_numeric_string_array_and_reference_constraints(self):
        doc = {'components': {'schemas': {'Amount': {'type': 'number', 'minimum': 0, 'exclusiveMinimum': True, 'maximum': 500}}}}
        schema = {'type': 'object', 'required': ['amount'], 'additionalProperties': False, 'properties': {
            'amount': {'$ref': '#/components/schemas/Amount'}, 'code': {'type': 'string', 'minLength': 2, 'maxLength': 4, 'pattern': '^[A-Z]+$'},
            'tags': {'type': 'array', 'minItems': 1, 'maxItems': 2, 'items': {'type': 'string'}}, 'memo': {'type': 'string', 'nullable': True},
            'count': {'allOf': [{'type': 'integer'}, {'minimum': 1}]}}}
        good = {'amount': 500, 'code': 'AB', 'tags': ['x'], 'memo': None, 'count': 1}
        self.assertEqual(contract.check_value(doc, schema, good), [])
        for change, expected in (({'amount': 0}, 'below the minimum'), ({'amount': 500.01}, 'above the maximum'), ({'code': 'A'}, 'shorter than 2'),
                                 ({'code': 'ABCDE'}, 'longer than 4'), ({'code': 'ab'}, 'does not match'), ({'tags': []}, 'fewer than 1'),
                                 ({'tags': ['a', 'b', 'c']}, 'more than 2'), ({'tags': [1]}, '$.tags[0]: expected string'), ({'count': 0}, 'below the minimum'),
                                 ({'count': 1.5}, 'expected integer'), ({'extra': 1}, 'field extra is not in the tool API')):
            self.assertIn(expected, ' | '.join(contract.check_value(doc, schema, dict(good, **change))), change)
        self.assertEqual(contract.check_value(doc, schema, {'amount': 1, 'extra': 1}, strict=False), ['$: field extra is not in the tool API'], 'additionalProperties false')

    def test_a_keyword_that_is_not_implemented_is_reported_not_passed(self):
        notes = []
        schema = {'type': 'object', 'properties': {'a': {'oneOf': [{'type': 'string'}, {'type': 'integer'}]}, 'b': {'type': 'string', 'format': 'uuid'}}}
        self.assertEqual(contract.check_value({}, schema, {'a': 1, 'b': 'x'}, notes=notes), [])
        self.assertEqual(notes, ["$.a: keyword 'oneOf' is not checked", "$.b: format 'uuid' is not checked"])

    def test_answers_of_the_stand_in_bank_have_the_documented_shape(self):
        bank = Bank('c1', {'date_of_birth': '1980-01-31', 'phone_last4': '1234'}, [{'type': 'credit_card', 'past_due': 100.0, 'days_late': 3}])
        for tool, body in (('verify_identity', VALID['verify_identity']), ('verify_identity', dict(VALID['verify_identity'], field='phone_last4', answer='1234')),
                           ('get_account', VALID['get_account']), ('create_promise', VALID['create_promise']), ('close_call', VALID['close_call'])):
            status, data = bank.call(tool, body)
            self.assertEqual(contract.check_response(API, tool, status, data), [], tool)
        self.assertEqual(contract.check_response(API, 'get_account', 500, None), [])
        self.assertEqual(contract.check_response(API, 'get_account', 404, {}), ['get_account: status 404 is not documented'])
        self.assertIn('response.ok: expected boolean', contract.check_response(API, 'get_account', 200, {'ok': 'yes'})[0])


class Tools(unittest.TestCase):
    def test_compiled_tools_agree_with_the_tool_api(self):
        for brief_b in (False, True):
            workflow, report = build_workflow(fixture(brief_b), OPENAPI)
            self.assertEqual(contract.check_tools(workflow, API), [])
            self.assertEqual(report['contract'], [])

    def test_a_compiled_tool_that_departs_from_the_tool_api_fails_the_build(self):
        workflow, report = build_workflow(fixture(True), OPENAPI)
        workflow['nodes']['negotiate']['tools'][0]['function']['parameters']['properties']['body']['properties']['amount']['type'] = 'string'
        with mock.patch.object(pipeline, 'compile_workflow', return_value=(workflow, {'nodes': [], 'bytes': 0, 'tools': {}})):
            with self.assertRaises(CompileError) as ctx:
                build_workflow(fixture(True), OPENAPI)
        self.assertIn('the compiled tools do not match the tool API: create_promise.amount: type string, the tool API says number', str(ctx.exception))

    def test_a_tool_that_departs_from_the_tool_api_is_named(self):
        workflow, _ = build_workflow(fixture(True), OPENAPI)
        tools = {t['function']['name']: t['function']['parameters']['properties'] for t in workflow['nodes']['negotiate']['tools']}
        tools['create_promise']['body']['properties']['amount']['type'] = 'string'
        tools['create_promise']['body']['properties']['currency'] = {'type': 'string', 'default': '{{currency}}'}
        tools['create_promise']['url']['default'] = BASE + 'promises'
        del tools['add_note']['body']['properties']['reason']
        tools['transfer_to_agent']['body']['properties']['queue']['enum'].append('billing')
        tools['transfer_to_agent']['method']['default'] = 'PUT'
        tools['close_call']['body']['required'] = ['outcome', 'reason']
        text = ' | '.join(contract.check_tools(workflow, API))
        for expected in ('create_promise.amount: type string, the tool API says number', 'create_promise: body field currency is not in the tool API',
                         'create_promise: url is not', 'add_note: body field reason of the tool API is missing',
                         'transfer_to_agent.queue: offers values the tool API does not allow: billing', 'transfer_to_agent: method is not POST',
                         'close_call: reason is required of the model but not by the tool API'):
            self.assertIn(expected, text)

    def test_every_request_of_a_played_call_agrees_with_the_tool_api(self):
        case = CASES['PUB-01']
        workflow, _ = build_workflow(fixture(), OPENAPI)
        sent = []

        class Recording(Sim):
            def _respond(self, tool, config, variables=None):
                sent.append((tool['function']['name'], {'url': config['url'], 'method': tool['function']['parameters']['properties']['method']['default'], 'body': dict(config['body'])}))
                return Sim._respond(self, tool, config, variables)

        bank = Bank('c1', case['identity'], case['products'])
        sim = Recording(workflow, bank, case['variables'])
        for field, answer in case['identity'].items():
            sim.tool('verify_identity', field=field, answer=answer)
        product = bank.products[0]
        sim.tool('create_promise', product_id='p1', amount=str(product['past_due']), date='2026-10-15')
        sim.tool('close_call', outcome='promise')
        self.assertEqual([name for name, _ in sent], ['verify_identity', 'verify_identity', 'get_account', 'create_promise', 'close_call'])
        for name, req in sent:
            self.assertEqual(contract.check_request(API, name, req), [], name)
        self.assertIsInstance(sent[3][1]['body']['amount'], float, 'an amount the model sent as text leaves as a number')

    def test_constructs_the_builder_does_not_handle_are_reported(self):
        self.assertEqual(contract.document_notes(API), [])
        doc = copy.deepcopy(OPENAPI)
        doc['components'] = {'securitySchemes': {'key': {'type': 'apiKey'}}}
        doc['paths']['/customers/{id}/close_call'] = doc['paths'].pop('/close_call')
        doc['paths']['/customers/{id}/close_call']['post'].update(operationId='close_call', parameters=[{'name': 'id', 'in': 'path'}], security=[{'key': []}])
        doc['paths']['/add_note']['post']['requestBody']['content'] = {'text/plain': {}}
        doc['paths']['/create_promise']['post']['requestBody']['content']['application/json']['schema']['properties']['split'] = {'type': 'array'}
        text = ' | '.join(contract.document_notes(Api(doc)))
        for expected in ('declares security schemes', 'close_call: path parameter id is not sent', 'close_call: the path /customers/{id}/close_call has a template',
                         'close_call: the operation requires credentials', 'add_note: the request body is not application/json', 'create_promise: body field split is nested'):
            self.assertIn(expected, text)


if __name__ == '__main__':
    unittest.main()
