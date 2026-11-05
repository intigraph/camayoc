# Current structural component setup

Run `sh docs/demos/current-structural-setup/run.sh` from a checkout.
The real setup script finds only the current structural executable in the
private fixture and writes the current MCP command and server name.
The binary is a synthetic presence stub; this demonstrates configuration,
not installation or a running MCP service. The installer stub refuses calls.

Replay the actual terminal recording after downloading `output.txt` and
`timing.txt` with `scriptreplay --log-timing timing.txt --log-out output.txt`.
The recording retains terminal CRLF bytes and its temporary fixture is removed.
