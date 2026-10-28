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
    "slack-webhook": j("https://hooks.sl", "ack.com/services/", "T0ABC1234/", "B0DEF5678/", R[:24]),
    "npm-token": j("np", "m_", R[:36]),
    "huggingface-token": j("h", "f_", R[:34]),
    "gitlab-token": j("gl", "pat-", R[:20]),
    "high-entropy-string": R,
    "base64-blob": j(R[:20], "+", R[20:39], "Q="),
}

CLEAN = [
    "http://aegis.gastown.local/ontology/mem/directive/crew-deploy-window",
    "https://github.com/intigraph/camayoc/pull/178",
    "sha256:" + "0123456789abcdef" * 4,
    "83c555ea0b1d2e3f4a5b6c7d8e9f00112233aabb",
    "Disruptive deploys go in the 02:00-09:00 ET window; never ask for one.",
    "Run sd locally; the board is seeds on quipu.svc.",
    "The API key lives in the secret store under the key name below.",
    # Review of #183: ordinary locators the whole-path entropy rule refused.
    "~/workspace/resume/applications/2026-10-12-Zep/notes.md",
    "https://github.com/intigraph/camayoc/blob/" + "83c555ea0b1d2e3f4a5b6c7d8e9f00112233aabb" + "/README.md",
    "s3://datalore-backups-501843416807/Postgres/2026/10/28/Full.tar.zst",
    "https://claude.ai/code/session_" + "018sJJsAdbcAyJnsCtDy2C1g",
    "http://aegis.gastown.local/ontology/mem/decision/repo-goldblum/push-policy/20261028T080313Z",
    "/Users/Steve/Library/Application Support/Claude/claude_desktop_config.json",
    "https://raw.githubusercontent.com/intigraph/bobbin/0123456789abcdefABCDEF0123456789abcdefAB/docs/X.md",
]

# The real-token battery: unlabeled values a locator might carry by mistake.
# Each must still be refused once the path rules stop scoring whole paths.
BATTERY = {
    "github classic": j("gh", "p_", R[:36]),
    "github oauth": j("gh", "o_", R[1:37]),
    "github fine-grained": j("github", "_pat_", "11ABCDEFG0", R[:59]),
    "aws key id": j("AK", "IA", "ZQ3RX7TW2YV6UP4N"),
    "aws secret, slash only": j("wJ8lrXUtnF", "/K7MDENGbPxRfi", "/CY3Xa9pLe2KqT8vZb", "Rm"),
    "aws secret with plus": j("u7Qx2Lr9Kd", "+Wm4Tz8Ns1Pb6Vy3Hc5Jg0", "Fa2Xe7Rk"),
    "slack bot": j("xo", "xb-", "2401234567-", R[:20]),
    "slack app": j("xa", "pp-", "1-A0123-", R[:24]),
    "slack webhook": POSITIVE["slack-webhook"],
    "openai": j("s", "k-", R[:40]),
    "openai project": j("s", "k-proj-", R[:40]),
    "anthropic api": j("s", "k-ant-api03-", R[:40]),
    "anthropic admin": j("s", "k-ant-admin01-", R[:40]),
    "google": j("AI", "za", R[3:38]),
    "stripe": j("s", "k_live_", R[:30]),
    "jwt": POSITIVE["jwt"],
    "npm": POSITIVE["npm-token"],
    "huggingface": POSITIVE["huggingface-token"],
    "base64 of 32 bytes": j("q3Vt8Zx1Lm", "Pk4Rn7Ws2Yb", "9Hc6Jd0Fg5Te", "Ua3Xi8Ko1E=").replace("E=", "E="),
    "labeled password": j("pass", "word=", "hunter2hunter2"),
    "url with password": POSITIVE["url-userinfo-password"],
    "token in a url query": j("https://svc.example/api?k=", R[:40]),
    "token in a plain path": j("~/notes/", R),
}


class SecretRules(unittest.TestCase):
    def test_every_named_rule_has_a_fixture(self):
        self.assertEqual({n for n, _ in msr.RULES} | {"high-entropy-string", "base64-blob"}, set(POSITIVE))

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

    def test_real_token_battery_still_refused_without_echo(self):
        for name, value in BATTERY.items():
            with self.subTest(token=name):
                self.assertTrue(msr.find_secrets(f"see {value} here"), name)
                lines = msr.refusals({"locator": value})
                self.assertEqual(1, len(lines))
                self.assertNotIn(value, lines[0])

    def test_secret_ref_name_passes(self):
        self.assertEqual(msr.refusals({"secretRef": "MEDIA_API_KEY", "locator": "http://svc.local/api"}), [])

    def test_secret_ref_value_refused(self):
        lines = msr.refusals({"secretRef": POSITIVE["github-token"]})
        self.assertEqual(lines, ["Not recorded: secretRef must be a secret-store key name, not a value."])


if __name__ == "__main__":
    unittest.main()
