"""One bounded native change subscription; SQLite stays on its owner thread.

The deployment supplies an existing journal and independently verified store
identity. This transport never bootstraps, resets, polls, or projects RDF.
"""
from __future__ import annotations

import json
import queue
import threading
import time
import urllib.parse
import urllib.request

import change_frames
import seed_change_custody as custody


def admitted_page(page, identity):
    """Materialize only inside the existing native custody budget, never widen it."""
    if (not isinstance(identity, dict) or set(identity) != {'store_id', 'log_epoch'}
            or any(not isinstance(v, str) or not v for v in identity.values())
            or not isinstance(page, dict) or page.get('capture') != 'old_and_new_values'
            or not isinstance(page.get('source'), dict)
            or any(page['source'].get(k) != v for k, v in identity.items())):
        raise custody.CustodyUnknown('stream source identity or capture changed')
    records = page.get('records')
    if not isinstance(records, (list, change_frames.StagedRecords)):
        raise custody.CustodyUnknown('stream records malformed')
    if len(records) > custody.MAX_RECORDS:
        raise custody.CustodyUnknown('whole transaction exceeds native custody budget')
    rows, size = [], 0
    for row in records:
        size += len(json.dumps(row).encode())
        if size > custody.MAX_BYTES:
            raise custody.CustodyUnknown('whole transaction exceeds native custody budget')
        rows.append(row)
    out = {**page, 'records': rows}
    if len(json.dumps(out).encode()) > custody.MAX_BYTES:
        raise custody.CustodyUnknown('whole transaction exceeds native custody budget')
    return out


class Subscription:
    def __init__(self, server, graph, cursor, identity, headers):
        if (not isinstance(identity, dict) or set(identity) != {'store_id', 'log_epoch'}
                or any(not isinstance(v, str) or not v for v in identity.values())):
            raise custody.CustodyUnknown('explicit verified stream identity required')
        if type(cursor) is not int or not 0 <= cursor <= change_frames.MAX_CURSOR:
            raise custody.CustodyUnknown('existing durable cursor required')
        self.server, self.graph, self.cursor = server, graph, cursor
        self.identity, self.headers = dict(identity), dict(headers)
        self.pending = queue.Queue(maxsize=1)
        self.stop = threading.Event()
        self.connected = False
        self.source_verified = False
        self.errors = 0
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()

    def _read(self):
        backoff = 1
        while not self.stop.is_set():
            try:
                query = urllib.parse.urlencode({'since': self.cursor, 'graph': self.graph})
                req = urllib.request.Request(self.server + '/changes/stream?' + query,
                    headers={**self.headers, 'Accept': 'text/event-stream',
                             'Last-Event-ID': str(self.cursor)})
                with urllib.request.urlopen(req, timeout=45) as response:
                    if response.headers.get_content_type() != 'text/event-stream':
                        raise custody.CustodyUnknown('stream content type refused')
                    self.connected = True
                    for stream_id, raw in change_frames.frames(response, max_transaction_bytes=custody.MAX_BYTES):
                        try:
                            page = admitted_page(raw, self.identity)
                            self.source_verified = True
                            if (type(stream_id) is not int or stream_id <= self.cursor
                                    or page.get('next_tx') != stream_id):
                                raise custody.CustodyUnknown('stream transaction boundary refused')
                            done = threading.Event()
                            self.pending.put((stream_id, page, done), timeout=45)
                            while not done.wait(1):
                                if self.stop.is_set():
                                    return
                            if self.cursor != stream_id:
                                raise custody.CustodyUnknown('candidate custody not committed')
                            backoff = 1
                        finally:
                            if isinstance(raw.get('records'), change_frames.StagedRecords):
                                raw['records'].close()
                raise custody.CustodyUnknown('stream disconnected')
            except Exception:
                # Never retain transport exception text: it may contain authority.
                self.connected = False
                self.source_verified = False
                self.errors += 1
                if self.stop.wait(backoff):
                    return
                backoff = min(60, backoff * 2)

    # A complete transaction that did not fit pending custody. While it is held
    # the reader stays blocked on its acknowledgement, so no later transaction
    # is read (backpressure) and this one is neither dropped nor reordered.
    held = None

    @property
    def paused(self):
        return self.held is not None

    def retain_one(self, journal, timeout=1):
        if self.held is not None:
            stream_id, page, done = self.held
        else:
            try:
                stream_id, page, done = self.pending.get(timeout=timeout)
            except queue.Empty:
                return False
        release = True
        try:
            if journal.cursor != self.cursor:
                raise custody.CustodyUnknown('concurrent candidate cursor changed')
            try:
                journal.retain(page)
            except custody.CustodyFull:
                # Pause admission only while effect ticks can drain something;
                # a transaction larger than all of custody stays a refusal.
                if journal.next() is None:
                    raise
                self.held, release = (stream_id, page, done), False
                time.sleep(timeout)  # Same pacing as an idle queue wait.
                return False
            if journal.cursor != stream_id:
                raise custody.CustodyUnknown('candidate transaction not retained')
            self.cursor = stream_id  # Only after atomic candidate+cursor commit.
            return True
        finally:
            if release:
                self.held = None
                done.set()

    def close(self):
        self.stop.set()
