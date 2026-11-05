# Request pricing demonstration

Two local synthetic Claude request fixtures share a task: one exact published
model, one unknown model. The base reports 7,100 tokens without prices. The
candidate preserves that total, prices 3,550 tokens at USD 0.0088, and leaves the
complete dollar total unknown. Plan share also remains unknown.

Base: `63f200d18671179827a05fd6b717a23f4a8e078c`. Candidate: the pricing change
containing this demonstration; no service, invoice, graph or provider call.
Run `python3 docs/demos/work-cost-prices/demo.py` from this checkout.

[Actual terminal output](output.txt) and [timing](timing.txt) are retained here.
Download both and replay with
`scriptreplay --log-out output.txt --log-timing timing.txt`.
The demo is source-level evidence; installed acceptance remains separate.
