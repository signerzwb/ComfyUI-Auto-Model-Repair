import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workflow_agent.security import safe_model_filename, validate_download_url, validate_model_folder


class SecurityTests(unittest.TestCase):
    def test_allows_trusted_https(self):
        self.assertEqual(
            validate_download_url("https://huggingface.co/org/repo/model.safetensors"),
            "https://huggingface.co/org/repo/model.safetensors",
        )
        self.assertEqual(validate_model_folder("loras"), "loras")

    def test_rejects_ssrf_and_unsafe_weights(self):
        for url in ("http://huggingface.co/a", "https://localhost/a", "https://huggingface.co.evil.test/a"):
            with self.assertRaises(ValueError):
                validate_download_url(url)
        for name in ("../model.ckpt", "model.pt", "model.exe"):
            with self.assertRaises(ValueError):
                safe_model_filename(name)

    def test_sanitizes_filename(self):
        self.assertEqual(safe_model_filename("folder/model.safetensors"), "model.safetensors")


if __name__ == "__main__":
    unittest.main()
