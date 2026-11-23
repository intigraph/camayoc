# Exact native closure records

**Status: source implementation; no lifecycle producer activation.**

The canonical exact-subject reader now retains the native `schema:endTime`
and `schema:result` fields alongside its metadata. A lifecycle consumer
calls `record.closure_record()` explicitly. The existing `tracker_record()`
metadata projection keeps its prior keys and values.

A closure projection requires closed status and the stored close timestamp.
It never substitutes `schema:dateModified` for the close time. The close
timestamp must be an offset instant between creation and modification,
including native nanosecond precision. It preserves the original timestamp
spelling and optional reason. Missing reason stays `None`; conflicting
or malformed fields refuse rather than selecting one value.

Metadata records without a close timestamp remain readable, but cannot
supply closure evidence. All fields come from the same bounded exact-subject
query and its existing per-evaluation cache. Live and positively proven
archive reads share this behavior. No discovery query or extra request is
added.

This is a projection of the hydrated item's current closed state. It does
not establish complete close-event coverage: an item closed and reopened
before hydration is currently open. Lifecycle acquisition still needs its
own admitted candidate source, dedicated durable custody, frozen retry
bodies and verified acknowledgement. The metadata producer's journal must
not be reused for lifecycle effects. This source change opens no stream,
initializes no custody and changes no schedule.

Run the private controls with:

```sh
python3 -m unittest discover -s tests -p test_seed_closure_record.py -v
```
