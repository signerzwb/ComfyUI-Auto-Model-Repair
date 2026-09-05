import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workflow_agent.routes import _official_comfy_file, _verified_file_candidate


class ModelSourceVerificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_known_comfy_org_repo_requires_exact_nested_file(self):
        requirement = {"expected": "minimax_h3_audio_vae_fp32.safetensors", "role": "vae"}
        file = {"name": "vae/minimax_h3_audio_vae_fp32.safetensors", "safe": True, "download_url": "https://www.modelscope.cn/api/v1/models/Comfy-Org/MiniMax-H3/repo?Revision=master&FilePath=vae%2Fminimax_h3_audio_vae_fp32.safetensors", "suggested_folder": "vae"}
        with patch("workflow_agent.routes.model_files", new=AsyncMock(return_value={"files": [file]})):
            result = await _official_comfy_file(requirement, [])
        self.assertEqual(result["id"], "Comfy-Org/MiniMax-H3")
        self.assertEqual(result["direct_file"], file)

    async def test_rejects_title_only_candidate_without_exact_file(self):
        requirement = {"expected": "minimax_h3_audio_vae_fp32.safetensors", "role": "vae", "family": "minimax"}
        candidate = {"provider": "civitai", "id": "123", "name": "MiniMax H3 workflow", "type": "Workflow"}
        with patch("workflow_agent.routes.model_files", new=AsyncMock(return_value={"files": [{"name": "another_model.safetensors", "safe": True, "download_url": "https://civitai.com/api/download/models/1"}]})):
            result = await _verified_file_candidate(requirement, candidate)
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
