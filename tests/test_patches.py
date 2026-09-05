import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workflow_agent.patches import SnapshotStore, validate_patch_plan


class PatchTests(unittest.TestCase):
    def test_rejects_unknown_nodes_and_types(self):
        workflow = {"nodes": [{"id": 1, "type": "Known"}]}
        result = validate_patch_plan({"operations": [{"kind": "replace_node", "node_id": 2, "target_type": "Known"}]}, workflow, {"Known"})
        self.assertFalse(result["valid"])

    def test_rejects_invalid_agent_operation_fields(self):
        workflow = {"nodes": [{"id": 1, "type": "Known"}], "links": []}
        result = validate_patch_plan(
            {"operations": [{"kind": "connect", "origin_id": 1, "target_id": 999, "origin_slot": "bad"}, {"kind": "set_widget", "node_id": 1, "widget_index": -1, "value": "x"}]},
            workflow,
            {"Known"},
        )
        self.assertFalse(result["valid"])
        self.assertEqual(result["operations"], [])

    def test_snapshot_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SnapshotStore(Path(directory))
            created = store.create({"nodes": [{"id": 1}]}, "测试")
            self.assertEqual(store.get(created["id"])["workflow"]["nodes"][0]["id"], 1)


if __name__ == "__main__":
    unittest.main()
