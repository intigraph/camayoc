# Check an event before admitting it

This 20-second recording calls the existing adapter source API with two public synthetic CloudEvents. A deployment maps to DeploymentEvent; an unknown type is quarantined with exit 2. The documentation helper prints selected mapped fields without posting an episode or contacting Quipu.

Use Python 3.11+. Previewing an event does not prove admission into a graph.

From the repository root, prepare a new workspace:

```sh
mkdir -p /tmp/camayoc-demo/scripts /tmp/camayoc-demo/mappings
cp scripts/ingest_cdevents.py scripts/planes.py scripts/windows.py scripts/quipu_auth.py /tmp/camayoc-demo/scripts/
cp mappings/cdevents.json /tmp/camayoc-demo/mappings/
cp docs/demos/terminal/preview-event.py /tmp/camayoc-demo/
cp docs/demos/terminal/fixture/*.json /tmp/camayoc-demo/
touch /tmp/camayoc-demo/.terminal-demo-sandbox
python3 docs/demos/terminal/render.py docs/demos/terminal/demo.tape.json \
  --workspace /tmp/camayoc-demo --tool python3="$(command -v python3)" \
  --output /tmp/camayoc-render
```

The manifest pins the executable and every source input. Deliberate reading holds differ from execution times. The unchanged canonical stack-terminal-24 recorder supplies a private HOME/XDG environment and refuses private paths, hosts and credentials. Read [the complete transcript](terminal/transcript.txt) for a static alternative. Recorder source: Tambo b0765b5d8f16a6a26acb3595552f17826d965b44, SHA-256 bebbe000565314f18a58731f2a1a4690577db127b3673fa10a45ce74eef0f23c.
