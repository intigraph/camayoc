#!/bin/sh
set -eu
repo=$(CDPATH= cd -- "$(dirname -- "$0")/../../.." && pwd)
fixture=$(mktemp -d /tmp/structural-setup-demo.XXXXXX)
trap 'rm -rf "$fixture"' EXIT
mkdir "$fixture/bin"
printf '#!/bin/sh\nexit 0\n' > "$fixture/bin/yupana"
printf '#!/bin/sh\necho unexpected-install >&2\nexit 91\n' > "$fixture/bin/cargo"
chmod 700 "$fixture/bin/yupana" "$fixture/bin/cargo"
CLAUDE_PROJECT_DIR="$fixture" PATH="$fixture/bin:$PATH" bash "$repo/scripts/setup_component.sh" yupana
python3 - "$fixture/.mcp.json" <<'PYCODE'
import json,sys
with open(sys.argv[1]) as stream: actual=json.load(stream)
assert actual == {"mcpServers":{"yupana":{"command":"yupana","args":["serve"]}}},actual
print(json.dumps(actual,sort_keys=True))
PYCODE
