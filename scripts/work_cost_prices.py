"""Price the existing request measurements; never infer missing usage or rates."""
from collections import Counter
from datetime import date
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path

TABLE = Path(__file__).resolve().parents[1] / 'data/model-prices.json'
PROVIDERS = {'claude': 'anthropic', 'codex': 'openai', 'agy': 'google'}
COUNTS = ('input_uncached', 'cache_read_input', 'cache_write_input', 'output')


def amount(value):
    if isinstance(value, bool):
        raise ValueError('invalid monetary amount')
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError('invalid monetary amount') from exc
    if not result.is_finite() or result < 0:
        raise ValueError('invalid monetary amount')
    return result


def request_price(record, table, today=None):
    """Return dollars or an explicit unknown reason; all counts are disjoint."""
    model = table['models'].get(record.get('model'))
    if model is None:
        return None, 'unlisted model'
    if model['provider'] != PROVIDERS.get(record.get('harness')):
        return None, 'model/provider mismatch'
    if model.get('valid_through') and (today or date.today()) > date.fromisoformat(model['valid_through']):
        return None, 'published promotional rate expired'
    counts = record.get('counts', {})
    if any(not isinstance(counts.get(k), int) or isinstance(counts[k], bool) or counts[k] < 0 for k in COUNTS):
        return None, 'unknown token category'
    detail = record.get('detail') or {}
    mode = detail.get('pricing') or {}
    speed, tier, geo = mode.get('speed'), mode.get('service_tier'), mode.get('inference_geo')
    if speed not in ('standard', 'fast') or tier != 'standard' or geo not in ('global', 'us'):
        return None, 'unknown or unsupported pricing modifiers'
    if speed == 'fast' and model.get('fast_multiplier') is None:
        return None, 'unpublished fast-mode rate'
    if model['provider'] == 'google' and geo != 'global':
        return None, 'unpublished regional rate'
    rate = {k: amount(v) if v is not None else None for k, v in model['usd_per_million'].items()}
    inp, read, write, out = (counts[k] for k in COUNTS)
    if write:
        split = detail.get('cache_creation')
        names = ('ephemeral_5m_input_tokens', 'ephemeral_1h_input_tokens') if model['provider'] == 'anthropic' else ('ephemeral_30m_input_tokens', 'ephemeral_12h_input_tokens')
        if not isinstance(split, dict) or any(not isinstance(split.get(k), int) or isinstance(split[k], bool) or split[k] < 0 for k in names):
            return None, 'unknown cache-write lifetime'
        short, long = (split[k] for k in names)
        if short + long != write:
            return None, 'cache-write lifetime counts disagree'
        if rate['cache_write_short'] is None or rate['cache_write_long'] is None:
            return None, 'unpublished cache-write rate'
    else:
        short = long = 0
    prompt = inp + read + write
    context = model.get('long_context')
    im = amount(context['input_multiplier']) if context and prompt > context['threshold'] else Decimal(1)
    om = amount(context['output_multiplier']) if context and prompt > context['threshold'] else Decimal(1)
    cost = (inp * rate['input'] + read * rate['cache_read'] +
            short * (rate['cache_write_short'] or Decimal(0)) +
            long * (rate['cache_write_long'] or Decimal(0))) * im + out * rate['output'] * om
    if speed == 'fast':
        cost *= amount(model['fast_multiplier'])
    if geo == 'us':
        cost *= Decimal('1.1')
    return cost / Decimal(1_000_000), None


def project(records, path=TABLE):
    raw = Path(path).read_bytes()
    table = json.loads(raw)
    if table.get('schema_version') != 1 or table.get('currency') != 'USD':
        raise ValueError('unsupported price table')
    groups, priced_records = {}, []
    for record in records:
        basis = 'inferred-held' if record.get('attribution') == 'inferred held item' else 'declared' if record.get('attribution') == 'attributed' else 'unattributed'
        key = tuple(record.get(k) for k in ('rig', 'bead', 'agent', 'harness', 'model')) + (basis,)
        group = groups.setdefault(key, dict(zip(('rig', 'bead', 'agent', 'harness', 'model', 'basis'), key)))
        group.setdefault('known_usd', Decimal(0))
        group.setdefault('priced_tokens', 0)
        group.setdefault('tokens', 0)
        group.setdefault('unknown_reasons', Counter())
        group.setdefault('requests', 0)
        group['requests'] += 1
        cost, reason = request_price(record, table)
        priced_records.append({**{k: record.get(k) for k in ('id', 'session', 'agent', 'harness')},
                               'usd': None if reason else str(cost), 'reason': reason})
        group['tokens'] += record['tokens']
        if reason:
            group['unknown_reasons'][reason] += 1
        else:
            group['known_usd'] += cost
            group['priced_tokens'] += record['tokens']
    result = []
    totals = {basis: {'known_usd': Decimal(0), 'tokens': 0, 'priced_tokens': 0, 'unknown_requests': 0, 'requests': 0} for basis in ('declared', 'inferred-held', 'unattributed')}
    for key in sorted(groups, key=lambda k: tuple(str(v) for v in k)):
        group = groups[key]
        total = totals[group['basis']]
        for field in ('known_usd', 'tokens', 'priced_tokens', 'requests'):
            total[field] += group[field]
        total['unknown_requests'] += sum(group['unknown_reasons'].values())
        group['usd'] = None if group['unknown_reasons'] else str(group['known_usd'])
        group['known_usd'] = str(group['known_usd'])
        group['unknown_reasons'] = dict(group['unknown_reasons'])
        result.append(group)
    for total in totals.values():
        total['usd'] = None if total['unknown_requests'] or not total['requests'] else str(total['known_usd'])
        total['known_usd'] = str(total['known_usd'])
    tokens = sum(g['tokens'] for g in result)
    priced = sum(g['priced_tokens'] for g in result)
    unknown = sum(t['unknown_requests'] for t in totals.values())
    known = sum((Decimal(t['known_usd']) for t in totals.values()), Decimal(0))
    return {'schema_version': 1, 'currency': 'USD', 'basis': table['basis'],
            'price_table_sha256': hashlib.sha256(raw).hexdigest(), 'retrieved_at': table['retrieved_at'],
            'groups': result, 'records': priced_records, 'totals': totals, 'tokens': tokens, 'priced_tokens': priced,
            'usd': str(known) if result and not unknown else None, 'known_usd': str(known),
            'unpriced_models': sorted({g['model'] or 'UNKNOWN' for g in result if g['unknown_reasons']}),
            'priced_token_share': priced / tokens if tokens else None,
            'plan_share_usd': None, 'plan_share_reason': 'no independently measured account-window share and plan allocation supplied'}
