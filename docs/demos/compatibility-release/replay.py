"""Replay retained asciicast-v2 output; no terminal-input or credential replay."""
import json
from pathlib import Path
import sys
import time
rows = Path(sys.argv[1]).read_text().splitlines()
assert json.loads(rows[0])["version"] == 2
previous = 0.0
for row in rows[1:]:
    stamp, kind, text = json.loads(row)
    assert kind == "o"
    time.sleep(min(max(stamp - previous, 0), 0.01))
    sys.stdout.write(text)
    sys.stdout.flush()
    previous = stamp
