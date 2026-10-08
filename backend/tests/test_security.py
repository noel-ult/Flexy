from __future__ import annotations

import unittest

from app.security import new_job_id, new_secret, safe_download_filename, secret_matches, token_hash


class SecurityPrimitiveTests(unittest.TestCase):
    def test_capability_is_opaque_and_hash_checked(self) -> None:
        secret = new_secret()
        self.assertTrue(secret_matches(secret, token_hash(secret)))
        self.assertFalse(secret_matches(secret + "x", token_hash(secret)))
        self.assertNotEqual(new_job_id(), new_job_id())

    def test_download_filename_cannot_inject_paths_or_headers(self) -> None:
        self.assertEqual(safe_download_filename("../../evil\r\n.txt", "fallback"), "evil.txt")
        self.assertEqual(safe_download_filename("", "fallback"), "fallback")


if __name__ == "__main__":
    unittest.main()
