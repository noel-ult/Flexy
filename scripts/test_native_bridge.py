"""Host bridge security checks; no live network, QEMU or package execution."""

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "native_bridge", Path(__file__).with_name("native_bridge.py")
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state = Path(self.temp.name)
        self.state.chmod(0o700)
        for name in ("worker-token", "id_ed25519", "known_hosts"):
            path = self.state / name
            path.write_text("t" * 43)
            path.chmod(0o600)

    def tearDown(self):
        self.temp.cleanup()

    def test_requires_https_origin_without_credentials(self):
        for origin in (
            "http://localhost",
            "https://user:pass@example.com",
            "https://example.com/api",
            "https://example.com?x=y",
            "https://example.com#fragment",
        ):
            with self.assertRaises(RuntimeError):
                module.Bridge(origin, self.state)

    def test_private_credentials_and_no_links(self):
        key = self.state / "worker-token"
        key.chmod(0o644)
        with self.assertRaises(RuntimeError):
            module.Bridge("https://example.com", self.state)
        key.unlink()
        key.symlink_to(self.state / "known_hosts")
        with self.assertRaises(RuntimeError):
            module.Bridge("https://example.com", self.state)

    def test_no_redirects_or_agent_forwarding(self):
        bridge = module.Bridge("https://example.com", self.state)
        self.assertIn("ForwardAgent=no", bridge.ssh)
        self.assertIn("StrictHostKeyChecking=yes", bridge.ssh)
        self.assertIn("ClearAllForwardings=yes", bridge.ssh)
        with self.assertRaises(RuntimeError):
            module.NoRedirect().redirect_request(
                None, None, 302, None, None, "https://evil.invalid"
            )

    def test_invalid_claim_never_downloads_or_executes(self):
        bridge = module.Bridge("https://example.com", self.state)
        with (
            patch.object(bridge, "request") as http,
            patch.object(module.subprocess, "Popen") as command,
        ):
            with self.assertRaises(RuntimeError):
                bridge.execute(
                    {
                        "id": "../../escape",
                        "lease": "t" * 43,
                        "recipeId": module.RECIPE,
                        "size": 100,
                        "sha256": "0" * 64,
                    }
                )
            http.assert_not_called()
            command.assert_not_called()


if __name__ == "__main__":
    unittest.main()
