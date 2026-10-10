"""builder/spec/openapi.py: references, naming and schema edge cases of the bank tool API."""
import copy
import json
import unittest
from unittest import mock

from builder.pipeline import build_workflow
from builder.spec.models import TOOLS, SpecError
from builder.spec.openapi import Api
from builder.validation.spec import validate
from tests.fixture_spec import spec as fixture
from tests.support import OPENAPI


def body_schema(doc, tool):
    return doc['paths']['/' + tool]['post']['requestBody']['content']['application/json']['schema']


def problems(spec, doc):
    with unittest.TestCase().assertRaises(SpecError) as ctx:
        validate(spec, Api(doc))
    return ' | '.join(ctx.exception.problems)


class Reading(unittest.TestCase):
    def test_operations_urls_fields_and_required(self):
        api = Api(OPENAPI)
        self.assertEqual(set(TOOLS), set(api.ops))
        self.assertEqual(api.url('create_promise'), 'https://freya-bank-mock.freyavoice.workers.dev/v1/create_promise')
        props, required = api.body('create_promise')
        self.assertEqual(list(props), ['customer_id', 'product_id', 'amount', 'date'])
        self.assertEqual(required, ['customer_id', 'product_id', 'amount', 'date'])
        self.assertEqual(props['date']['format'], 'date')
        self.assertEqual(api.body('verify_identity')[0]['field']['enum'], ['date_of_birth', 'phone_last4'])
        self.assertIn('minimum_payment', api.product_schema('products'))
        self.assertIn('wrong_answers_left', api.response('verify_identity')['properties'])

    def test_local_references_are_followed_everywhere(self):
        doc = copy.deepcopy(OPENAPI)
        doc['components'] = {'schemas': {
            'Promise': body_schema(doc, 'create_promise'),
            'Amount': {'type': 'number', 'description': 'money'},
            'Product': doc['paths']['/get_account']['post']['responses']['200']['content']['application/json']['schema']['properties']['products']['items'],
            'Account': {'type': 'object', 'properties': {'ok': {'type': 'boolean'}, 'products': {'type': 'array', 'items': {'$ref': '#/components/schemas/Product'}}}}}}
        doc['components']['schemas']['Promise']['properties']['amount'] = {'$ref': '#/components/schemas/Amount'}
        doc['paths']['/create_promise']['post']['requestBody']['content']['application/json']['schema'] = {'$ref': '#/components/schemas/Promise'}
        doc['paths']['/get_account']['post']['responses']['200']['content']['application/json']['schema'] = {'$ref': '#/components/schemas/Account'}
        api = Api(doc)
        self.assertEqual(api.body('create_promise')[0]['amount'], {'type': 'number', 'description': 'money'})
        self.assertIn('past_due', api.product_schema('products'))
        workflow, _ = build_workflow(fixture(), doc)
        leaf = workflow['nodes']['negotiate']['tools'][0]['function']['parameters']['properties']['body']['properties']['amount']
        self.assertEqual((leaf['type'], leaf['description']), ('number', 'money'))

    def test_a_dangling_or_circular_reference_does_not_hang_or_crash(self):
        doc = copy.deepcopy(OPENAPI)
        doc['components'] = {'schemas': {'Loop': {'$ref': '#/components/schemas/Loop'}}}
        doc['paths']['/add_note']['post']['requestBody']['content']['application/json']['schema'] = {'$ref': '#/components/schemas/Missing'}
        doc['paths']['/schedule_callback']['post']['requestBody']['content']['application/json']['schema'] = {'$ref': '#/components/schemas/Loop'}
        api = Api(doc)
        self.assertEqual(api.body('add_note'), ({}, []))
        self.assertEqual(api.body('schedule_callback')[0], {})
        self.assertIn("callback.date_param='date' is not a body field of schedule_callback", problems(fixture(), doc))

    def test_tool_is_named_by_operation_id_or_by_path(self):
        doc = copy.deepcopy(OPENAPI)
        doc['paths']['/accounts/lookup'] = doc['paths'].pop('/get_account')
        doc['paths']['/accounts/lookup']['post']['operationId'] = 'get_account'
        doc['paths']['/api/v9/close_call'] = doc['paths'].pop('/close_call')
        api = Api(doc)
        self.assertTrue(api.url('get_account').endswith('/v1/accounts/lookup'))
        self.assertTrue(api.url('close_call').endswith('/v1/api/v9/close_call'))
        workflow, _ = build_workflow(fixture(), doc)
        url = workflow['nodes']['get_account']['pre_actions'][0]['function']['parameters']['properties']['url']['default']
        self.assertTrue(url.endswith('/accounts/lookup'))

    def test_method_success_status_and_missing_bodies(self):
        doc = copy.deepcopy(OPENAPI)
        op = doc['paths']['/get_account'].pop('post')
        op['responses']['201'] = op['responses'].pop('200')
        doc['paths']['/get_account']['put'] = op
        doc['paths']['/get_account']['parameters'] = [{'name': 'x', 'in': 'query'}]
        api = Api(doc)
        self.assertEqual(api.ops['get_account']['method'], 'PUT')
        self.assertIn('days_late', api.product_schema('products'))
        del doc['paths']['/transfer_to_agent']['post']['requestBody']
        del doc['paths']['/verify_identity']['post']['responses']
        api = Api(doc)
        self.assertEqual(api.body('transfer_to_agent'), ({}, []))
        self.assertEqual(api.response('verify_identity'), {})
        text = problems(fixture(), doc)
        self.assertIn("transfer.queue_param='queue' is not a body field of transfer_to_agent", text)
        self.assertIn('identity.response.verified must name a verify_identity response field', text)

    def test_missing_tool_or_unsafe_server_is_an_explicit_error(self):
        doc = copy.deepcopy(OPENAPI)
        del doc['paths']['/add_note']
        doc['servers'] = [{'url': 'http://bank.invalid/v1'}]
        text = problems(fixture(), doc)
        self.assertIn('tool API has no operation add_note', text)
        self.assertIn('no https server url', text)
        doc['servers'] = []
        self.assertIn('no https server url', problems(fixture(), doc))
        self.assertIn('spec must be a JSON object', problems([], OPENAPI))

    def test_the_document_and_the_spec_are_never_modified(self):
        doc, spec = copy.deepcopy(OPENAPI), fixture(True)
        before = json.dumps(doc, sort_keys=True), json.dumps(spec, sort_keys=True)
        first, _ = build_workflow(spec, doc)
        second, _ = build_workflow(spec, doc)
        self.assertEqual(before, (json.dumps(doc, sort_keys=True), json.dumps(spec, sort_keys=True)))
        self.assertEqual(json.dumps(first, sort_keys=True), json.dumps(second, sort_keys=True))


if __name__ == '__main__':
    unittest.main()
