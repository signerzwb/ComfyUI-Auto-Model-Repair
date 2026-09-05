import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workflow_agent import manager


class ManagerTests(unittest.TestCase):
    def tearDown(self):
        manager.manager_catalog.cache_clear()

    def test_unknown_extension_map_entry_still_has_safe_manager_request(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "extension-node-map.json").write_text(json.dumps({"https://github.com/example/node": [["ExampleMissingNode"]]}), encoding="utf-8")
            (root / "custom-node-list.json").write_text(json.dumps({"custom_nodes": []}), encoding="utf-8")
            with patch.object(manager, "manager_root", return_value=root):
                manager.manager_catalog.cache_clear()
                candidate = manager.package_candidates("ExampleMissingNode")[0]
                request = manager.manager_install_request(candidate)
        self.assertEqual(request["body"]["version"], "unknown")
        self.assertEqual(request["body"]["selected_version"], "unknown")
        self.assertEqual(request["body"]["files"], ["https://github.com/example/node"])
