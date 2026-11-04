"""Published rates, missing evidence and aggregate coverage have distinct answers."""
from copy import deepcopy
from datetime import date
from decimal import Decimal
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import work_cost_prices as prices


def request(model='claude-opus-5-5', harness='claude', **changes):
    value = {'model': model, 'harness': harness, 'rig': 'test', 'bead': 'task-a', 'agent': 'worker',
             'attribution': 'attributed', 'tokens': 3550,
             'counts': {'input_uncached': 1000, 'cache_read_input': 2000,
                        'cache_write_input': 500, 'output': 50},
             'detail': {'cache_creation': {'ephemeral_5m_input_tokens': 200,
                                           'ephemeral_1h_input_tokens': 300},
                        'pricing': {'speed': 'standard', 'service_tier': 'standard', 'inference_geo': 'global'}}}
    value.update(changes)
    return value


class Prices(unittest.TestCase):
    def setUp(self):
        self.table = json.loads(prices.TABLE.read_text())

    def price(self, value):
        return prices.request_price(value, self.table, today=date(2026, 11, 4))

    def test_mixed_cache_lifetimes_and_fast_regional_modifiers(self):
        value = request()
        self.assertEqual(self.price(value), (Decimal('0.0088'), None))
        value['detail']['pricing'].update(speed='fast', inference_geo='us')
        self.assertEqual(self.price(value), (Decimal('0.01936'), None))

    def test_haiku_threshold_includes_cached_input(self):
        value = request('claude-haiku-5-5')
        value['counts'] = dict(input_uncached=100000, cache_read_input=0, cache_write_input=0, output=0)
        self.assertEqual(self.price(value), (Decimal('0.01'), None))
        value['counts'].update(input_uncached=99999, cache_read_input=2)
        self.assertEqual(self.price(value), (Decimal('0.0499996'), None))

    def test_openai_long_context_applies_to_whole_request(self):
        value = request('gpt-6.1-terra', 'codex')
        value['counts'] = dict(input_uncached=272000, cache_read_input=0, cache_write_input=0, output=100)
        self.assertEqual(self.price(value), (Decimal('0.2725'), None))
        value['counts']['input_uncached'] += 1
        self.assertEqual(self.price(value), (Decimal('0.544752'), None))

    def test_unknown_is_not_zero_and_model_names_are_exact(self):
        values = [request('claude-opus-unknown'), request('gemini-4-argon-high', 'agy'), request(harness='codex')]
        value = request(); value['detail']['pricing'].pop('speed'); values.append(value)
        value = request(); value['counts']['output'] = None; values.append(value)
        value = request(); value['detail']['cache_creation']['ephemeral_5m_input_tokens'] = 500; values.append(value)
        value = request(); value['detail'].pop('cache_creation'); values.append(value)
        value = request('claude-sonnet-5-5'); value['detail']['pricing']['speed'] = 'fast'; values.append(value)
        for value in values:
            with self.subTest(value=value):
                dollars, reason = self.price(value)
                self.assertIsNone(dollars)
                self.assertIsInstance(reason, str)

    def test_gemini_promotion_expires_and_regional_price_is_not_invented(self):
        value = request('gemini-4-argon', 'agy')
        value['counts'] = dict(input_uncached=1000, cache_read_input=2000, cache_write_input=0, output=50)
        self.assertEqual(self.price(value), (Decimal('0.0027'), None))
        self.assertIsNone(prices.request_price(value, self.table, date(2026, 12, 1))[0])
        value['detail']['pricing']['inference_geo'] = 'us'
        self.assertIsNone(self.price(value)[0])

    def test_projection_keeps_bases_separate_and_partial_totals_unknown(self):
        inferred = request(attribution='inferred held item')
        missing = request('unpriced')
        before = deepcopy([request(), inferred, missing])
        result = prices.project(before)
        self.assertEqual(before, [request(), inferred, missing])
        self.assertEqual(result['totals']['inferred-held']['usd'], '0.0088')
        self.assertIsNone(result['totals']['declared']['usd'])
        self.assertEqual(Decimal(result['known_usd']), Decimal('0.0176'))
        self.assertIsNone(result['usd'])
        self.assertEqual([r['usd'] for r in result['records']], ['0.0088', '0.0088', None])
        self.assertEqual(result['priced_tokens'], 7100)
        self.assertEqual(result['tokens'], 10650)
        self.assertEqual(result['unpriced_models'], ['unpriced'])
        self.assertIsNone(result['plan_share_usd'])

    def test_empty_sources_are_not_a_zero_dollar_observation(self):
        result = prices.project([])
        self.assertIsNone(result['usd'])
        self.assertIsNone(result['priced_token_share'])
        self.assertTrue(all(total['usd'] is None for total in result['totals'].values()))
