import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workflow_agent.providers import _modelscope_download_url, _modelscope_file_items


class ModelScopeProviderTests(unittest.TestCase):
    def test_keeps_nested_tree_entries(self):
        files = _modelscope_file_items({"Data": {"Files": [{"Path": "vae/minimax_h3_audio_vae_fp32.safetensors", "Type": "blob"}]}})
        self.assertEqual(files[0]["Path"], "vae/minimax_h3_audio_vae_fp32.safetensors")

    def test_generates_documented_file_download_url(self):
        url = _modelscope_download_url("Comfy-Org/MiniMax-H3", "vae/minimax h3.safetensors")
        self.assertIn("/api/v1/models/Comfy-Org/MiniMax-H3/repo?", url)
        self.assertIn("Revision=master", url)
        self.assertIn("FilePath=vae%2Fminimax+h3.safetensors", url)


if __name__ == "__main__":
    unittest.main()
