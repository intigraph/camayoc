"""memory_secret_rules: every named rule refuses, secretRef names pass, and
ordinary memory text (IRIs, URLs, digests, SHAs) is not refused.

Fixtures are assembled at runtime from fragments so no credential-shaped
literal sits in the repository."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import memory_secret_rules as msr

R = "Ab3dE5gH7jK9mN2pQ4sT6vW8yZ1bC3dF5hJ7kL9"  # mixed-class filler, 40 chars


def j(*parts: str) -> str:
    return "".join(parts)


POSITIVE = {
    "github-token": j("gh", "p_", R[:36]),
    "github-fine-grained-pat": j("github", "_pat_", R[:30]),
    "model-api-key": j("s", "k-", "ant-", R[:30]),
    "stripe-key": j("s", "k_", "live_", R[:24]),
    "slack-token": j("xo", "xb-", "1234567890-", R[:12]),
    "aws-access-key-id": j("AK", "IA", "ABCDEFGHIJ234567"),
    "google-api-key": j("AI", "za", R[:35]),
    "jwt": j("ey", "J", R[:16], ".", "ey", "J", R[2:18], ".", R[4:24]),
    "private-key-block": j("-----BEGIN ", "RSA PRIVATE", " KEY-----"),
    "bearer-credential": j("Authorization: Bear", "er ", R[:28]),
    "url-userinfo-password": j("https://svc:", "hunter2pass", "@example.invalid/x"),
    "labeled-assignment": j("pass", "word = ", "correcthorsebattery"),
    "high-entropy-string": R,
}

CLEAN = [
    "http://aegis.gastown.local/ontology/mem/directive/crew-deploy-window",
    "https://github.com/intigraph/camayoc/pull/178",
    "sha256:" + "0123456789abcdef" * 4,
    "83c555ea0b1d2e3f4a5b6c7d8e9f00112233aabb",
    "Disruptive deploys go in the 02:00-09:00 ET window; never ask for one.",
    "Run sd locally; the board is seeds on quipu.svc.",
    "The API key lives in the secret store under the key name below.",
]


class SecretRules(unittest.TestCase):
    def test_every_named_rule_has_a_fixture(self):
        self.assertEqual({n for n, _ in msr.RULES} | {"high-entropy-string"}, set(POSITIVE))

    def test_each_rule_refuses(self):
        for rule, value in POSITIVE.items():
            with self.subTest(rule=rule):
                self.assertIn(rule, msr.find_secrets(f"note: {value} end"))
                lines = msr.refusals({"locator": value})
                self.assertEqual(len(lines), 1)
                self.assertIn("--secret-ref", lines[0])
                self.assertNotIn(value, lines[0], "diagnostic must not echo the value")

    def test_clean_text_passes(self):
        for text in CLEAN:
            with self.subTest(text=text):
                self.assertEqual(msr.find_secrets(text), [])

    def test_secret_ref_name_passes(self):
        self.assertEqual(msr.refusals({"secretRef": "MEDIA_API_KEY", "locator": "http://svc.local/api"}), [])

    def test_secret_ref_value_refused(self):
        lines = msr.refusals({"secretRef": POSITIVE["github-token"]})
        self.assertEqual(lines, ["Not recorded: secretRef must be a secret-store key name, not a value."])


if __name__ == "__main__":
    unittest.main()
