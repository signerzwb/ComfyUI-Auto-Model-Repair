import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workflow_agent import environment


class EnvironmentTests(unittest.TestCase):
    def test_catalog_does_not_execute_untrusted_input_schema(self):
        class ExplosiveMeta(type):
            def __getattribute__(cls, name):
                if name not in {"__class__", "__name__", "__qualname__"}:
                    raise TypeError("Schema.__init__() got an unexpected keyword argument 'search_aliases'")
                return super().__getattribute__(name)

        class BrokenNode(metaclass=ExplosiveMeta):
            pass

        fake_nodes = types.ModuleType("nodes")
        fake_nodes.NODE_CLASS_MAPPINGS = {"BrokenLegacyNode": BrokenNode}
        with patch.dict(sys.modules, {"nodes": fake_nodes}):
            environment.node_catalog.cache_clear()
            catalog = environment.node_catalog()
        environment.node_catalog.cache_clear()
        self.assertIn("BrokenLegacyNode", catalog)
        self.assertEqual(catalog["BrokenLegacyNode"]["inputs"], {})
        self.assertEqual(catalog["BrokenLegacyNode"]["outputs"], [])
