"""Current structural component setup and intentional former-name alias controls."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class SetupComponent(unittest.TestCase):
    def run_setup(self, component):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            for name, body in (("yupana", "exit 0"), ("cargo", "exit 91")):
                tool = bin_dir / name
                tool.write_text("#!/bin/sh\n" + body + "\n")
                tool.chmod(0o700)
            env = dict(os.environ, CLAUDE_PROJECT_DIR=str(root),
                       PATH=str(bin_dir) + os.pathsep + os.environ["PATH"])
            result = subprocess.run(["bash", str(ROOT / "scripts/setup_component.sh"), component],
                                    env=env, capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return json.loads((root / ".mcp.json").read_text()), result.stdout

    def test_current_name_uses_current_executable_and_server(self):
        config, output = self.run_setup("yupana")
        self.assertEqual(config["mcpServers"], {"yupana": {"command": "yupana", "args": ["serve"]}})
        self.assertIn("yupana: done", output)

    def test_former_component_argument_is_an_explicit_compatibility_alias(self):
        # Deliberate old CLI argument compatibility; emit only the current server.
        config, _ = self.run_setup("hank")
        self.assertEqual(config["mcpServers"], {"yupana": {"command": "yupana", "args": ["serve"]}})


if __name__ == "__main__":
    unittest.main()
