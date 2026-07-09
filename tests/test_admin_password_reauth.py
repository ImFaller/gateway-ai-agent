import re
import unittest
from pathlib import Path


ADMIN_HTML = Path(__file__).resolve().parents[1] / "frontend" / "web" / "admin.html"


class AdminPasswordReauthTests(unittest.TestCase):
    def setUp(self):
        self.html = ADMIN_HTML.read_text(encoding="utf-8")

    def test_verified_password_is_not_cached_between_sensitive_actions(self):
        self.assertNotIn("function getCachedPassword", self.html)
        self.assertNotIn("function setCachedPassword", self.html)
        self.assertNotIn("setCachedPassword(", self.html)
        self.assertNotIn("if (cached) return cached", self.html)

    def test_verified_password_prompt_still_verifies_against_backend(self):
        match = re.search(
            r"async function getVerifiedPassword\(actionDesc\) \{(?P<body>.*?)\n\}",
            self.html,
            flags=re.S,
        )
        self.assertIsNotNone(match)
        body = match.group("body")

        self.assertIn("promptVerifyPassword", body)
        self.assertIn("API + '/auth/verify'", body)
        self.assertIn("return pwd", body)


if __name__ == "__main__":
    unittest.main()
