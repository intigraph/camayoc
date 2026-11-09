# Native feed capacity replay

Run `python3 docs/demos/native-feed-capacity/demo.py`. Base `f6372f6e43e762385b0b573c2ca5432afff544c9` stops at cursor10 with 171 pending when a 100-item page exceeds the 256-item cap. The candidate retries complete pages at 4, 2, 1 transactions, reaches cursor13 with 246 pending, and preserves the cap. The synthetic delivery is disabled; this proves feed custody behavior, not live catch-up.

[Output transcript](output.log) and [timing](timing.log) form an output-only terminal recording. Replay downloaded files with `scriptreplay --log-out output.log --log-timing timing.log`. No inline playback is claimed. Candidate identity is the containing source revision.
