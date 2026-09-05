import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workflow_agent.model_intelligence import extract_model_requirements, group_dependency_bundles, score_online_candidate, source_priority


class ModelIntelligenceTests(unittest.TestCase):
    def test_prefers_comfy_org_modelscope_source(self):
        self.assertEqual(source_priority({"provider": "modelscope", "id": "Comfy-Org/Wan2.2"})[0], 0)
        self.assertLess(source_priority({"provider": "modelscope", "id": "community/model"})[0], source_priority({"provider": "huggingface", "id": "org/model"})[0])
        self.assertLess(source_priority({"provider": "huggingface", "id": "org/model"})[0], source_priority({"provider": "civitai", "id": "1"})[0])

    def test_extracts_model_with_schema_context(self):
        workflow = {
            "nodes": [{"id": 1, "type": "CheckpointLoaderSimple", "widgets_values": ["flux_dev.safetensors"]}],
            "links": [],
        }
        catalog = {"CheckpointLoaderSimple": {"inputs": {"required": {"ckpt_name": [["x"]]}}, "outputs": ["MODEL", "CLIP", "VAE"]}}
        items = extract_model_requirements(workflow, catalog)
        self.assertEqual(items[0]["expected"], "flux_dev.safetensors")
        self.assertEqual(items[0]["role"], "checkpoints")
        self.assertEqual(items[0]["family"], "flux")

    def test_groups_related_requirements(self):
        bundles = group_dependency_bundles([
            {"node_id": "1", "family": "wan", "role": "diffusion_models"},
            {"node_id": "1", "family": "wan", "role": "vae"},
        ])
        self.assertEqual(len(bundles), 1)
        self.assertEqual(set(bundles[0]["roles"]), {"diffusion_models", "vae"})

    def test_rejects_incompatible_family(self):
        requirement = {"expected": "flux_dev.safetensors", "family": "flux", "role": "checkpoints"}
        result = score_online_candidate(requirement, {"name": "sdxl_base.safetensors", "type": "Checkpoint", "base_model": "SDXL"})
        self.assertFalse(result["compatible"])


if __name__ == "__main__":
    unittest.main()
