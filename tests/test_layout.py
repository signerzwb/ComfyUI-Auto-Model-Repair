import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workflow_agent.layout import build_layout_plan


class LayoutTests(unittest.TestCase):
    def test_builds_left_to_right_semantic_groups(self):
        workflow = {
            "nodes": [
                {"id": 1, "type": "CheckpointLoaderSimple", "pos": [800, 800], "size": [220, 100]},
                {"id": 2, "type": "CLIPTextEncode", "pos": [0, 0], "size": [220, 100]},
                {"id": 3, "type": "KSampler", "pos": [0, 0], "size": [220, 100]},
                {"id": 4, "type": "VAEDecode", "pos": [0, 0], "size": [220, 100]},
                {"id": 5, "type": "SaveImage", "pos": [0, 0], "size": [220, 100]},
            ],
            "links": [[1, 1, 0, 3, 0, "MODEL"], [2, 2, 0, 3, 1, "CONDITIONING"], [3, 3, 0, 4, 0, "LATENT"], [4, 4, 0, 5, 0, "IMAGE"]],
        }
        plan = build_layout_plan(workflow)
        self.assertGreater(plan["positions"]["5"][0], plan["positions"]["1"][0])
        self.assertGreaterEqual(plan["summary"]["group_count"], 3)
        self.assertEqual(plan["warning"].startswith("本次计划只修改"), True)

    def test_every_acyclic_link_reads_left_to_right_even_when_stage_names_conflict(self):
        # A downloaded workflow can use a post-processing/custom node before a
        # sampler.  Functional labels must never make that valid dependency
        # point back to the left.
        workflow = {
            "nodes": [
                {"id": 1, "type": "PostProcessor", "pos": [2000, 600], "size": [220, 100]},
                {"id": 2, "type": "KSampler", "pos": [0, 0], "size": [220, 100]},
                {"id": 3, "type": "SaveImage", "pos": [0, 0], "size": [220, 100]},
            ],
            "links": [[1, 1, 0, 2, 0, "LATENT"], [2, 2, 0, 3, 0, "IMAGE"]],
        }
        plan = build_layout_plan(workflow)
        self.assertLess(plan["positions"]["1"][0], plan["positions"]["2"][0])
        self.assertLess(plan["positions"]["2"][0], plan["positions"]["3"][0])

    def test_large_functional_stage_is_split_into_readable_flow_bands(self):
        workflow = {
            "nodes": [
                {"id": index, "type": "LoadImage", "pos": [0, index * 100], "size": [180, 80]}
                for index in range(1, 6)
            ],
            "links": [[index, index, 0, index + 1, 0, "IMAGE"] for index in range(1, 5)],
        }
        groups = build_layout_plan(workflow)["groups"]
        input_groups = [group for group in groups if group["stage_id"] == "input"]
        self.assertGreaterEqual(len(input_groups), 3)
        self.assertTrue(all("·" in group["title"] for group in input_groups))

    def test_is_stable(self):
        workflow = {"nodes": [{"id": 1, "type": "LoadImage", "pos": [1, 1], "size": [100, 80]}], "links": []}
        self.assertEqual(build_layout_plan(workflow)["positions"], build_layout_plan(workflow)["positions"])

    def test_preserve_groups_does_not_create_duplicate_groups(self):
        workflow = {"nodes": [{"id": 1, "type": "LoadImage", "pos": [1, 1], "size": [100, 80]}], "links": [], "groups": [{"title": "用户已有分组"}]}
        plan = build_layout_plan(workflow, mode="preserve_groups")
        self.assertEqual(plan["groups"], [])

    def test_flags_long_cross_stage_link_for_reroute_review(self):
        workflow = {
            "nodes": [
                {"id": 1, "type": "CheckpointLoaderSimple", "pos": [0, 0], "size": [220, 100]},
                {"id": 2, "type": "KSampler", "pos": [0, 0], "size": [220, 100]},
                {"id": 3, "type": "VAEDecode", "pos": [0, 0], "size": [220, 100]},
                {"id": 4, "type": "SaveImage", "pos": [0, 0], "size": [220, 100]},
            ],
            "links": [[1, 1, 0, 2, 0, "MODEL"], [2, 2, 0, 3, 0, "LATENT"], [3, 3, 0, 4, 0, "IMAGE"], [99, 1, 0, 4, 0, "MODEL"]],
        }
        self.assertTrue(build_layout_plan(workflow)["reroute_suggestions"])


if __name__ == "__main__":
    unittest.main()
