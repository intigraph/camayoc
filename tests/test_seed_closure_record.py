"""Closure projection controls, using complete private native records only."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import seed_work_items as native
from test_seed_work_items import rows, ID, SUBJECT

CLOSED_AT = native.SCHEMA + 'endTime'
REASON = native.SCHEMA + 'result'
STAMP = '2026-10-10T12:00:00.123456789Z'


class ClosureProjection(unittest.TestCase):
    def read(self, data):
        requests = []
        def post(endpoint, body):
            requests.append(body['query'])
            return {'rows': data, 'truncated': False}
        reader = native.Reader(post, native.Board('urn:private:board', 'demo'))
        return reader, requests

    def closed(self, **changes):
        return rows(**{native.SEEDS+'status': 'closed', CLOSED_AT: STAMP,
                       REASON: 'Exact stored reason', **changes})

    def test_stored_closure_survives_one_exact_read(self):
        reader, requests = self.read(self.closed())
        record = reader.get(ID)
        projected = record.closure_record()
        self.assertEqual(projected['id'], ID)
        self.assertEqual(projected['closed_at'], STAMP)
        self.assertEqual(projected['close_reason'], 'Exact stored reason')
        self.assertNotEqual(projected['closed_at'], projected['updated_at'])
        self.assertIn('<'+CLOSED_AT+'>', requests[0])
        self.assertIn('<'+REASON+'>', requests[0])
        self.assertEqual(len(requests), 1)
        self.assertIs(reader.get(ID), record)
        # Metadata consumers keep their established projection contract.
        self.assertNotIn('closed_at', record.tracker_record())
        self.assertNotIn('close_reason', record.tracker_record())

    def test_optional_reason_stays_absent_instead_of_being_invented(self):
        reader, _ = self.read(self.closed(**{REASON: None}))
        self.assertIsNone(reader.get(ID).closure_record()['close_reason'])

    def test_archived_closure_keeps_board_identity_and_stored_evidence(self):
        # As for get_archived(), the caller must already have positive archive proof.
        reader, requests = self.read(self.closed())
        projected = reader.get_archived(ID).closure_record()
        self.assertIn('GRAPH <urn:private:board#seeds-archive>', requests[0])
        self.assertEqual(projected['source_graph'], 'urn:private:board')
        self.assertEqual(projected['closed_at'], STAMP)
        self.assertEqual(projected['close_reason'], 'Exact stored reason')

    def test_missing_close_time_is_unknown_never_modified_time(self):
        reader, _ = self.read(self.closed(**{CLOSED_AT: None}))
        record = reader.get(ID)
        self.assertEqual(record.status, 'closed')  # Still usable as metadata.
        with self.assertRaises(native.NativeUnknown):
            record.closure_record()

    def test_only_closed_status_can_become_a_closure(self):
        for status in sorted(native.STATES - {'closed'}):
            with self.subTest(status=status):
                reader, _ = self.read(self.closed(**{native.SEEDS+'status': status}))
                with self.assertRaises(native.NativeUnknown):
                    reader.get(ID).closure_record()

    def test_close_time_must_be_an_offset_instant_inside_record_history(self):
        for stamp in ('not-a-date', '2026-10-10T12:00:00',
                      '2026-09-30T23:59:59Z', '2026-10-11T13:00:01Z',
                      '2026-10-11T13:00:00.123456790Z',
                      '2026-10-10T12:00:00.1234567890Z'):
            with self.subTest(stamp=stamp):
                reader, _ = self.read(self.closed(**{CLOSED_AT: stamp}))
                with self.assertRaises(native.NativeUnknown):
                    reader.get(ID).closure_record()

    def test_conflicting_or_malformed_closure_fields_refuse(self):
        for predicate, value in ((CLOSED_AT, '2026-10-10T12:01:00Z'),
                                 (REASON, 'Another reason')):
            data = self.closed() + [{'s': SUBJECT, 'p': predicate, 'o': value}]
            reader, _ = self.read(data)
            with self.assertRaises(native.NativeUnknown):
                reader.get(ID)
        for predicate in (CLOSED_AT, REASON):
            reader, _ = self.read(self.closed(**{predicate: {'ref': 'urn:wrong'}}))
            with self.assertRaises(native.NativeUnknown):
                reader.get(ID)

    def test_compact_predicates_and_typed_literals_round_trip(self):
        data = self.closed(**{CLOSED_AT: {'value': STAMP, 'datatype': 'xsd:dateTime'}})
        data = [{**row, 'p': 'schema:endTime'} if row['p'] == CLOSED_AT else row
                for row in data]
        reader, _ = self.read(data)
        self.assertEqual(reader.get(ID).closure_record()['closed_at'], STAMP)

    def test_equivalent_offsets_preserve_exact_stored_spelling(self):
        # Every accepted spelling stays unchanged; comparison alone uses UTC.
        for stamp in ('2026-10-01T00:00:00Z', '2026-10-01T01:00:00+01:00',
                      '2026-10-11T13:00:00.123456789Z'):
            reader, _ = self.read(self.closed(**{CLOSED_AT: stamp}))
            self.assertEqual(reader.get(ID).closure_record()['closed_at'], stamp)


if __name__ == '__main__':
    unittest.main()
