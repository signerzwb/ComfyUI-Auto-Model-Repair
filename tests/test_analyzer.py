import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workflow_agent.analyzer import analyze_workflow, normalize_links


class AnalyzerTests(unittest.TestCase):
    def test_normalizes_legacy_and_object_links(self):
        workflow = {
            "links": [
                [1, 10, 0, 11, 1, "MODEL"],
                {"id": 2, "originId": 11, "originSlot": 0, "targetId": 12, "targetSlot": 0},
            ]
        }
        links = normalize_links(workflow)
        self.assertEqual(links[0]["origin_id"], 10)
        self.assertEqual(links[1]["target_id"], 12)

    def test_finds_unknown_isolated_and_overlap(self):
        workflow = {
            "version": 0.4,
            "nodes": [
                {"id": 1, "type": "Known", "pos": [0, 0], "size": [200, 100]},
                {"id": 2, "type": "Missing", "pos": [20, 20], "size": [200, 100]},
            ],
            "links": [],
            "groups": [],
        }
        report = analyze_workflow(workflow, ["Known"])
        codes = {item["code"] for item in report["findings"]}
        self.assertIn("unknown_node_types", codes)
        self.assertIn("isolated_nodes", codes)
        self.assertIn("overlapping_nodes", codes)

    def test_duplicate_model_load(self):
        workflow = {
            "nodes": [
                {"id": 1, "type": "CheckpointLoaderSimple", "widgets_values": ["a.safetensors"]},
                {"id": 2, "type": "CheckpointLoaderSimple", "widgets_values": ["a.safetensors"]},
            ],
            "links": [[1, 1, 0, 2, 0, "MODEL"]],
        }
        report = analyze_workflow(workflow)
        self.assertIn("duplicate_model_load", {item["code"] for item in report["findings"]})


if __name__ == "__main__":
    unittest.main()
