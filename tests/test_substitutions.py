import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workflow_agent import substitutions


class SubstituteTests(unittest.TestCase):
    def test_offers_port_compatible_local_candidate(self):
        workflow = {
            "nodes": [{"id": 7, "type": "MissingImageTool", "title": "Image tool", "inputs": [{"type": "IMAGE"}], "outputs": [{"type": "IMAGE"}]}]
        }
        catalog = {
            "ImageScale": {"inputs": {"required": {"image": ["IMAGE"]}}, "outputs": ["IMAGE"], "category": "image"},
            "TextNode": {"inputs": {"required": {"text": ["STRING"]}}, "outputs": ["STRING"], "category": "text"},
        }
        with patch.object(substitutions, "resolve_missing_node_packages", return_value=[{"node_type": "MissingImageTool", "status": "unresolved", "candidates": [], "message": "未找到"}]):
            result = substitutions.resolve_missing_nodes(workflow, catalog)
        self.assertEqual(result[0]["local_substitutes"][0]["node_type"], "ImageScale")
        self.assertTrue(result[0]["local_substitutes"][0]["port_compatible"])
