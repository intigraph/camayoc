#!/usr/bin/env python3
"""Publish parser-owned request facts into Camayoc's observed plane.

Reads the SAME result used by retrieve_metric.py, never st-supplied counts.
A per-session snapshot replaces only this producer's facts, so a corrected
focus assignment cannot leave two work items charged for the same request.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from urllib.parse import quote

from ingest_session_usage import BASE, ONTOLOGY, READERS, emit
import planes
from work_cost import retrieve

MAX_SNAPSHOTS = 1
MAX_WORK_ITEMS = 8
MAX_RECORDS = 1000
MAX_BODY_BYTES = 4 * 1024 * 1024
CONSUMER = 'publish_work_cost.py'
READ_GRANT = 'read:crew:records'
REVIEW_SHAPES = 20
BUDGET_LIMITS = ('items', 'records', 'body_bytes', 'snapshots')


def require_read_grant():
    """Observed WorkItems are usable only by an explicitly authorized consumer.

    This grants a read, not a graph move or authority to promote inferred facts.
    The source plane stays intact and the cost actor cannot supply its own grant.
    """
    path = Path(os.environ.get('CAMAYOC_AUTHORITY',
                Path(__file__).resolve().parents[1] / 'config/plane-authority.json'))
    try:
        grants = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise ValueError('observed WorkItem read authority unavailable; refusing publication') from exc
    if (not isinstance(grants, dict)
            or not isinstance(grants.get(CONSUMER), list)
            or any(not isinstance(v, str) for v in grants[CONSUMER])
            or READ_GRANT not in grants[CONSUMER]):
        raise ValueError(f'{CONSUMER} lacks {READ_GRANT}; refusing publication')

PROPERTIES = {'input_uncached': 'inputTokensUncached', 'cache_read_input': 'cacheReadInputTokens',
              'cache_write_input': 'cacheWriteInputTokens', 'output': 'outputTokens'}


def snapshots(result, actor):
    if result['errors']:
        raise ValueError('cannot replace graph snapshot with incomplete source reads')
    sessions = {}
    for record in result['records']:
        key = (record['harness'], record['session'], record['agent'])
        sessions.setdefault(key, []).append(record)
    for (fmt, session_id, principal), records in sorted(sessions.items()):
        out = ['@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .']
        emit(session_id, principal, READERS[fmt][1], fmt, records, out)
        session_iri = f'{BASE}session/{quote(session_id, safe="")}'
        items = set()
        for record in records:
            iri = f'{session_iri}/usage/{quote(record["id"], safe="")}'
            out.append(f'<{iri}> <{ONTOLOGY}usageHarness> {json.dumps(fmt)} .')
            if record['model']:
                out.append(f'<{iri}> <{ONTOLOGY}usageModel> {json.dumps(record["model"])} .')
                out.append(f'<{iri}> <{ONTOLOGY}usageModelSource> {json.dumps(record["model_source"])} .')
            for kind, value in record['counts'].items():
                if value is not None:
                    out.append(f'<{iri}> <{ONTOLOGY}{PROPERTIES[kind]}> {value} .')
            if record['attribution'] == 'attributed':
                item = record['bead']
                # Canonical tracker mapper uses the exact stable item ID.
                if not item or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.' for c in item):
                    raise ValueError('unsafe tracker identifier')
                out.append(f'<{iri}> <{ONTOLOGY}attributedTo> <{ONTOLOGY}{item}> .')
                items.add(item)
        # WorkItem types come from ingest_work_items.py in this SAME observed
        # plane. Never copy them into a replaceable cost snapshot: retracting
        # that snapshot must not disturb canonical tracker identity facts.
        turtle = '\n'.join(out)
        yield {'turtle': turtle, 'actor': actor, 'source': f'camayoc-session-usage:{fmt}:{session_id}',
               'graph': planes.plane_for('observed'), 'replace_snapshot': True,
               'snapshot': f'camayoc-session-usage:{fmt}:{session_id}',
               'shapes': (Path(__file__).resolve().parents[1] / 'shapes/usage-breakdown.shapes.ttl').read_text()}, records


def expand_item(value):
    # The query endpoint compacts this registered namespace on the wire.
    # Normalize only the namespace we own; unknown prefixes stay mismatches.
    return ONTOLOGY + value[len('aegis:'):] if value.startswith('aegis:') else value


class OperatorAction(ValueError):
    """A reviewed budget requires narrower operator-selected sources."""


def _atomic(path, text):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(text)
    temporary.replace(path)


def record_refusals(history, receipt):
    """Count decisions since installation; old uncaptured refusals stay unknown."""
    accounting = history.setdefault('refusal_accounting', {
        'since': receipt.get('attempted_at'), 'attempts': 0, 'refusals': 0,
        'limits': dict.fromkeys(BUDGET_LIMITS, 0), 'recent_refusals': []})
    window = history.get('review_window_started_at')
    if 'window' not in accounting or accounting['window']['started_at'] != window:
        accounting['window'] = {'started_at': window, 'observed_since': receipt.get('attempted_at'),
                                'baseline_attempts': accounting['attempts'],
                                'baseline_refusals': accounting['refusals']}
    decision = receipt.get('budget_decision')
    if decision in ('admitted', 'refused'):
        accounting['attempts'] += 1
    if decision == 'refused':
        accounting['refusals'] += 1
        for limit in receipt['budget_limits']:
            accounting['limits'][limit] += 1
        accounting['recent_refusals'].append({
            'attempted_at': receipt['attempted_at'], 'limits': receipt['budget_limits'],
            'sources': receipt.get('budget_sources', [])[:MAX_SNAPSHOTS + 1],
            'sources_truncated': len(receipt.get('budget_sources', [])) > MAX_SNAPSHOTS + 1,
            **{key: receipt[key][:MAX_SNAPSHOTS + 1] for key in
               ('items_per_snapshot', 'records_per_snapshot', 'body_bytes_per_snapshot')}})
        accounting['recent_refusals'] = accounting['recent_refusals'][-REVIEW_SHAPES:]
    return accounting


def record_budget(history, receipt):
    """Preserve lifetime maxima and retain the first twenty distinct shapes.

    Legacy run samples remain intact. Only their measured tuples seed the
    distinct population; maxima cannot reconstruct missing historical samples.
    After saturation the distinct count is a lower bound, with bounded storage.
    """
    record_refusals(history, receipt)
    history.setdefault('runs', 0)
    samples = history.setdefault('samples', [])
    candidate = None
    if receipt['records_per_snapshot']:
        reached = bool(receipt.get('preflight_reached'))
        history['runs'] += int(reached)
        for key in ('items', 'records', 'body_bytes'):
            history['max_' + key] = max(history.get('max_' + key, 0),
                                      *receipt[key + '_per_snapshot'], 0)
        if reached:
            candidate = {'run': history['runs'], 'attempted_at': receipt['attempted_at'],
                         **{key: max(receipt[key + '_per_snapshot'], default=0)
                            for key in ('items', 'records', 'body_bytes')}}
            if len(samples) < REVIEW_SHAPES:
                samples.append(candidate)
    distinct = history.setdefault('distinct_samples', [])
    shape = lambda sample: tuple(sample[key] for key in ('items', 'records', 'body_bytes'))
    seen = {shape(sample) for sample in distinct}
    for sample in [*samples, *([candidate] if candidate else [])]:
        key = shape(sample)
        if key not in seen and len(distinct) < REVIEW_SHAPES:
            distinct.append(dict(sample))
            seen.add(key)
    history['distinct_shapes'] = len(distinct)
    history['distinct_shapes_saturated'] = len(distinct) >= REVIEW_SHAPES
    history['review_due'] = history['distinct_shapes_saturated']
    return history


def publish(result, actor, state_path, *, push_status=False, review_only=False,
            scheduler_only=None, generation_policy=None, _lane_locked=False):
    """Every attempt leaves a receipt and status metrics, including failures."""
    if not _lane_locked:
        from usage_generation_step import locked
        lane = locked(state_path)
        try:
            lane.__enter__()
        except ValueError as exc:
            # Another run holds the lane. Leave a receipt for this attempt too,
            # without touching the budget history the holder owns; the holder's
            # own receipt replaces this one when it finishes.
            now = time.time()
            _atomic(state_path.with_suffix('.receipt.json'), json.dumps(
                {'attempted_at': now, 'finished_at': now, 'status': 'OPERATOR_ACTION',
                 'reason': 'busy', 'detail': str(exc), 'requests': 0}, sort_keys=True))
            raise
        try:
            return publish(result,actor,state_path,push_status=push_status,review_only=review_only,
                           scheduler_only=scheduler_only,generation_policy=generation_policy,_lane_locked=True)
        finally:
            lane.__exit__(None, None, None)
    receipt_path = state_path.with_suffix('.receipt.json')
    receipt = {'attempted_at': time.time(), 'status': 'UNKNOWN', 'requests': 0,
               'request_seconds': 0, 'items_per_snapshot': [], 'records_per_snapshot': [],
               'body_bytes_per_snapshot': [], 'readback': 'sampled-last-record'}
    try:
        if scheduler_only is not None:
            if scheduler_only not in {'tick', 'preflight', 'source_selection', 'projection'}:
                raise ValueError('unknown scheduler status')
            previous = json.loads(receipt_path.read_text()) if receipt_path.exists() else {}
            if 'next_request_after' in previous:
                receipt['next_request_after'] = previous['next_request_after']
            receipt['scheduler_only'] = scheduler_only
            if scheduler_only == 'tick':
                # A heartbeat is not a new successful projection. Keep the last
                # outcome while selection runs, including a previous failure.
                receipt.update(status=previous.get('status', 'UNKNOWN'),
                               detail=previous.get('detail', 'awaiting first projection'),
                               unknown_reason=previous.get('unknown_reason', 'not_yet_published'))
            else:
                receipt.update(status='UNKNOWN', unknown_reason=scheduler_only,
                               detail=f'scheduled sampler failed during {scheduler_only}')
            if state_path.with_suffix('.pending.json').exists():
                receipt['status'] = 'STALLED'
            return []
        if review_only:
            history = json.loads(state_path.with_suffix('.budget.json').read_text())
            if not history.get('review_due') or len(history.get('distinct_samples', [])) < REVIEW_SHAPES:
                raise ValueError('review-only status requires a completed bounded population')
            if state_path.with_suffix('.pending.json').exists():
                raise ValueError('indeterminate snapshot requires reconciliation before review')
            previous = json.loads(receipt_path.read_text()) if receipt_path.exists() else {}
            if 'next_request_after' in previous:
                receipt['next_request_after'] = previous['next_request_after']
            receipt.update(status='OK', detail='review boundary reached; graph publication paused')
            return []
        previous = json.loads(receipt_path.read_text()) if receipt_path.exists() else {}
        if previous.get('next_request_after', 0) > time.time():
            receipt['next_request_after'] = previous['next_request_after']
            for field in ('items_per_snapshot', 'records_per_snapshot', 'body_bytes_per_snapshot'):
                receipt[field] = previous.get(field, [])
            receipt['dimensions_source'] = 'previous_attempt'
            raise OperatorAction('BACKOFF: previous request budget cooldown has not elapsed')
        answer = _publish(result, actor, state_path, receipt, generation_policy=generation_policy, _lane_locked=_lane_locked)
        receipt['status'] = 'UNKNOWN' if receipt.get('unknown_reason')=='staging' else 'OK'
        return answer
    except OperatorAction as exc:
        receipt.update(status='STALLED' if state_path.with_suffix('.pending.json').exists() else 'OPERATOR_ACTION',
                       detail=str(exc))
        raise
    except (OSError, ValueError, planes.PlaneError) as exc:
        receipt.update(status='STALLED' if state_path.with_suffix('.pending.json').exists() else 'UNKNOWN',
                       detail=str(exc))
        raise
    finally:
        receipt['finished_at'] = time.time()
        if receipt['requests']:
            slow = receipt.get('knot_seconds', 0) > 10 or receipt['request_seconds'] > 15
            receipt['next_request_after'] = receipt['finished_at'] + 900 if slow else receipt['first_request_at'] + 60
            receipt['backoff_seconds'] = 900 if slow else 60
        reason = ('pending' if receipt['status'] == 'STALLED' else
                  'backoff' if receipt.get('detail', '').startswith('BACKOFF:') else
                  'budget' if receipt['status'] == 'OPERATOR_ACTION' else
                  'error' if receipt['status'] == 'UNKNOWN' else 'none')
        receipt['reason'] = reason
        history_path = state_path.with_suffix('.budget.json')
        history = json.loads(history_path.read_text()) if history_path.exists() else {'runs': 0}
        record_budget(history, receipt)
        _atomic(history_path, json.dumps(history, sort_keys=True))
        receipt['budget_history'] = history
        _atomic(receipt_path, json.dumps(receipt, sort_keys=True))
        metrics = '\n'.join([
            '# TYPE camayoc_cost_projection_stalled gauge',
            f'camayoc_cost_projection_stalled{{reason="{reason}"}} {int(reason in {"pending", "budget", "error"})}', 
            '# TYPE camayoc_cost_preflight_runs gauge',
            f"camayoc_cost_preflight_runs {history['runs']}",
            '# HELP camayoc_cost_distinct_shapes Observed distinct snapshot shapes; lower bound at 20.',
            '# TYPE camayoc_cost_distinct_shapes gauge',
            f"camayoc_cost_distinct_shapes {history['distinct_shapes']}",
            '# TYPE camayoc_cost_budget_review_due gauge',
            f"camayoc_cost_budget_review_due {int(history['review_due'])}",
            '# TYPE camayoc_cost_projection_ok gauge',
            f"camayoc_cost_projection_ok {int(receipt['status'] == 'OK')}",
            '# TYPE camayoc_cost_projection_unknown gauge',
            f'camayoc_cost_projection_unknown{{reason="{receipt.get("unknown_reason", "publisher")}"}} {int(receipt["status"] == "UNKNOWN")}',
            '# TYPE camayoc_cost_projection_last_attempt_timestamp_seconds gauge',
            f"camayoc_cost_projection_last_attempt_timestamp_seconds {receipt['attempted_at']}", ''])
        accounting = history['refusal_accounting']
        metrics += '\n'.join([
            '# HELP camayoc_cost_budget_attempts_total Changed invocations admitted or refused by size preflight since accounting began.',
            '# TYPE camayoc_cost_budget_attempts_total counter',
            f"camayoc_cost_budget_attempts_total {accounting['attempts']}",
            '# HELP camayoc_cost_budget_refusals_total Invocations refused before graph network access; admitted shapes are censored.',
            '# TYPE camayoc_cost_budget_refusals_total counter',
            f"camayoc_cost_budget_refusals_total {accounting['refusals']}",
            '# HELP camayoc_cost_budget_limit_refusals_total Binding limits at the first refusal; one refusal may bind multiple limits.',
            '# TYPE camayoc_cost_budget_limit_refusals_total counter',
            *[f'camayoc_cost_budget_limit_refusals_total{{limit="{limit}"}} {accounting["limits"][limit]}'
              for limit in BUDGET_LIMITS],
            '# TYPE camayoc_cost_budget_accounting_since_timestamp_seconds gauge',
            f"camayoc_cost_budget_accounting_since_timestamp_seconds {accounting['since'] or 0}",
            '# TYPE camayoc_cost_budget_window_attempts gauge',
            f"camayoc_cost_budget_window_attempts {accounting['attempts'] - accounting['window']['baseline_attempts']}",
            '# TYPE camayoc_cost_budget_window_refusals gauge',
            f"camayoc_cost_budget_window_refusals {accounting['refusals'] - accounting['window']['baseline_refusals']}",
            '# TYPE camayoc_cost_budget_window_observed_since_timestamp_seconds gauge',
            f"camayoc_cost_budget_window_observed_since_timestamp_seconds {accounting['window']['observed_since'] or 0}", ''])
        _atomic(state_path.with_suffix('.prom'), metrics)
        print('cost publication receipt: ' + json.dumps(receipt, sort_keys=True), file=sys.stderr)
        if push_status:
            from camayoc_metrics import push
            ok, why = push('camayoc_cost_projection', metrics, grouping={'producer': actor})
            print('cost status metrics: ' + why, file=sys.stderr)
            if not ok:
                raise RuntimeError('cost status metrics were not delivered: ' + why)


def _publish(result, actor, state_path, receipt, *, generation_policy=None, shared_budget=False, _lane_locked=False):
    require_read_grant()
    if generation_policy is not None:
        from usage_generation_publisher import policy_check, tick
        policy_check(generation_policy)
        rows=result.get('records',[])
        matches=all(r.get('harness')==generation_policy['harness'] and r.get('session')==generation_policy['session'] for r in rows)
        items={r.get('bead') for r in rows if r.get('attribution')=='attributed'}
        journal=state_path.with_suffix('.generations.json')
        owned=False
        if journal.exists():
            from usage_generation_publisher import load_state
            from usage_generation_codec import digest
            state=load_state(journal)
            owned=digest([generation_policy['producer'],generation_policy['harness'],generation_policy['session']]) in state['sessions']
        if matches and (len(items)>MAX_WORK_ITEMS or owned):
            return tick(result,actor,state_path,receipt,generation_policy,lane_locked=_lane_locked)
        return _publish(result,actor,state_path,receipt,shared_budget=True,_lane_locked=_lane_locked)
    generation_journal=state_path.with_suffix('.generations.json')
    if generation_journal.exists():
        from usage_generation_publisher import load_state
        journal=load_state(generation_journal)
        identities={(r.get('harness'),r.get('session')) for r in result.get('records',[])}
        for entry in journal['sessions'].values():
            owner=entry.get('progress',{}).get('plan',{}).get('manifest',{}) if entry.get('progress') else entry.get('selected',{})
            if not isinstance(owner,dict) or not owner.get('harness') or not owner.get('session'):
                raise ValueError('UNKNOWN: unreadable generation ownership; no legacy fallback')
            if (owner['harness'],owner['session']) in identities:
                raise ValueError('legacy whole-session publication frozen for generation-owned session')
    if state_path.with_suffix('.pending.json').exists():
        raise ValueError('previous graph write indeterminate; reconcile pending snapshot before retry')
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    transactions = []
    pending = []
    for body, records in snapshots(result, actor):
        encoded = json.dumps(body, sort_keys=True)
        digest = hashlib.sha256(encoded.encode()).hexdigest()
        items = sorted({r['bead'] for r in records if r['attribution'] == 'attributed'})
        receipt['items_per_snapshot'].append(len(items))
        receipt['records_per_snapshot'].append(len(records))
        receipt['body_bytes_per_snapshot'].append(len(encoded.encode()))
        if state.get(body['snapshot']) == digest:
            continue
        receipt.setdefault('budget_sources', []).append({
            'snapshot': body['snapshot'], 'items': len(items),
            'records': len(records), 'body_bytes': len(encoded.encode()),
            **{key: records[0].get(key) if records else None
               for key in ('agent', 'harness', 'session')}})
        limits = [key for key, measured, cap in (
            ('items', len(items), MAX_WORK_ITEMS), ('records', len(records), MAX_RECORDS),
            ('body_bytes', len(encoded.encode()), MAX_BODY_BYTES)) if measured > cap]
        if limits:
            receipt.update(budget_decision='refused', budget_limits=limits)
            raise OperatorAction('cost snapshot exceeds reviewed item/record/byte budget; narrow sources')
        pending.append((body, records, digest, items))
    if len(pending) > MAX_SNAPSHOTS:
        receipt.update(budget_decision='refused', budget_limits=['snapshots'])
        raise OperatorAction('cost run exceeds reviewed snapshot budget; narrow sources')
    if pending:
        receipt['budget_decision'] = 'admitted'
    # Preflight the complete invocation BEFORE any network access. Backfills
    # must be explicitly partitioned; no N-session query burst or partial run.
    last_request = None

    def post(endpoint, body):
        nonlocal last_request
        if endpoint == '/query':
            # WorkItem ingress and usage snapshots share this named plane.
            # An omitted graph queries ROOT and falsely reports them absent.
            body = {**body, 'graph': planes.plane_for('observed')}
        if last_request is not None:
            time.sleep(max(0, 1 - (time.monotonic() - last_request)))
        last_request = time.monotonic()
        if receipt['requests'] >= 3:
            raise OperatorAction('cost request budget exhausted')
        receipt.setdefault('first_request_at', time.time())
        receipt['requests'] += 1
        started = time.monotonic()
        try:
            return planes._post(endpoint, body, client='camayoc-cost')
        finally:
            elapsed = time.monotonic() - started
            receipt['request_seconds'] += elapsed
            if endpoint == '/knot':
                receipt['knot_seconds'] = elapsed

    for body, records, digest, items in pending:
        key = body['snapshot']
        marker = state_path.with_suffix('.pending.json')
        if marker.exists():
            raise ValueError('previous graph write indeterminate; reconcile pending snapshot before retry')
        # Unattributed requests still consume a real snapshot workload. The
        # budget preflight above covers them even without a WorkItem query.
        receipt['preflight_reached'] = True
        if items:
            # Named rows preserve which item is missing; FILTER proves direct
            # typing without inference. No conjunction across distinct items.
            values = ' '.join(f'<{ONTOLOGY}{item}>' for item in items)
            check = post('/query', {'query': f'SELECT ?item WHERE {{ VALUES ?item {{ {values} }} '
                          f'?item a ?t . FILTER(?t = <{ONTOLOGY}WorkItem>) }}'})
            if not isinstance(check.get('rows'), list):
                raise ValueError('INDETERMINATE: canonical preflight returned no rows field')
            found = {expand_item(row['item']) for row in check['rows'] if row.get('item')}
            expected = {ONTOLOGY + item for item in items}
            if not found:
                control = post('/query', {'query': f'SELECT ?t WHERE {{ <{ONTOLOGY}{items[0]}> a ?t . '
                                         f'FILTER(?t = <{ONTOLOGY}WorkItem>) }}'})
                # Even two zeros cannot establish absence with an unproven
                # control. Never prescribe re-ingestion on this arm.
                raise ValueError('INDETERMINATE: zero-of-N canonical preflight; single-item control '
                                 + ('present' if control.get('rows') else 'unproven'))
            if found != expected:
                missing = sorted(item.removeprefix(ONTOLOGY) for item in expected - found)
                raise ValueError('canonical WorkItem unavailable: ' + ', '.join(missing))
        # Store an indeterminate marker BEFORE sending. A failed write is not
        # retried automatically; the exact snapshot and read-back must be
        # reconciled by the operator before clearing this marker.
        marker = state_path.with_suffix('.pending.json')
        if marker.exists():
            raise ValueError('previous graph write indeterminate; reconcile pending snapshot before retry')
        if shared_budget:
            from usage_generation_budget import admit
            ledger_path=state_path.with_suffix('.writes.json')
            ledger=json.loads(ledger_path.read_text()) if ledger_path.exists() else []
            next_ledger=admit('whole',len(json.dumps(body,sort_keys=True).encode()),len(records),3,time.time(),ledger)
        marker.write_text(json.dumps(body, sort_keys=True))
        if shared_budget:
            _atomic(ledger_path,json.dumps(next_ledger,sort_keys=True))
        response = post('/knot', body)
        if response.get('conforms') is False or not response.get('tx_id'):
            raise ValueError('graph refused cost snapshot')
        # A response proves acceptance; separately check one exact request's
        # total before advancing the cursor. No blind retry on failure.
        record = records[-1]
        iri = f'{BASE}session/{quote(record["session"], safe="")}/usage/{quote(record["id"], safe="")}'
        check = post('/query', {'query': f'SELECT ?n ?item WHERE {{ <{iri}> <{ONTOLOGY}tokensConsumed> ?n . OPTIONAL {{ <{iri}> <{ONTOLOGY}attributedTo> ?item }} }}'})
        rows = check.get('rows', [])
        if not any(str(row.get('n')) == str(record['tokens']) for row in rows):
            raise ValueError('cost snapshot accepted but read-back unproven')
        items_seen = {expand_item(row['item']) for row in rows if row.get('item')}
        expected = {ONTOLOGY + record['bead']} if record['attribution'] == 'attributed' else set()
        if items_seen != expected:
            raise ValueError('cost snapshot attribution read-back differs; reconcile pending snapshot')
        state[key] = digest
        temporary = state_path.with_suffix('.tmp')
        temporary.write_text(json.dumps(state, sort_keys=True))
        temporary.replace(state_path)
        marker.unlink()
        transactions.append(response['tx_id'])
    return transactions


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('method', type=Path, nargs='?')
    ap.add_argument('--generation-policy',type=Path,help='Explicit reviewed A runtime enrollment; absent keeps C unchanged')
    ap.add_argument('--actor', required=True)
    ap.add_argument('--state', type=Path, required=True)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument('--post', action='store_true')
    mode.add_argument('--review-only', action='store_true', help='refresh completed-review status without source or graph access')
    mode.add_argument('--scheduler-only', choices=('tick', 'preflight', 'source_selection', 'projection'),
                      help='record scheduler liveness or early failure without source or graph access')
    ap.add_argument('--publish-status', action='store_true', help='push attempt status, including stalled attempts')
    args = ap.parse_args()
    try:
        if args.scheduler_only:
            answer = publish(None, args.actor, args.state, push_status=args.publish_status,
                             scheduler_only=args.scheduler_only)
            print(json.dumps(answer))
            return 0
        if args.review_only:
            answer = publish(None, args.actor, args.state, push_status=args.publish_status, review_only=True)
            print(json.dumps(answer))
            return 0
        if args.method is None:
            raise ValueError('method is required outside review-only status')
        method = json.loads(args.method.read_text())
        if method.get('system') != 'session_usage' or method.get('query') != 'work_cost':
            raise ValueError('expected session_usage/work_cost method')
        result = retrieve(method['params'])
        answer = publish(result, args.actor, args.state, push_status=args.publish_status, generation_policy=json.loads(args.generation_policy.read_text()) if args.generation_policy else None) if args.post else [b for b, _ in snapshots(result, args.actor)]
        print(json.dumps(answer, sort_keys=True))
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(f'cost publication UNKNOWN: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
