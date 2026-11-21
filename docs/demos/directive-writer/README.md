Source-only demonstration: the same explicit deadline argument previously disappeared
from the generated fields and emitted record. It now becomes one Quechua typed
literal. Reads of issuer/topic/current versions are mocked; record() and its
serialization are real. No live writes or installation are exercised.

Run `python docs/demos/directive-writer/run.py` in the development environment.
Replay with `scriptreplay --log-out output.log --log-timing timing.log`.
The offline positive/negative aggregate controls are in
`tests/test_directive_writer_deadline.py`.
