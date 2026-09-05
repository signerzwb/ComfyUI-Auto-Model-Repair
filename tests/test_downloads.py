import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workflow_agent.downloads import _content_range


class _Response:
    def __init__(self, value):
        self.headers = {"Content-Range": value}


class DownloadTransportTests(unittest.TestCase):
    def test_accepts_modelscope_200_style_content_range(self):
        self.assertEqual(_content_range(_Response("bytes 1024-2047/8192")), (1024, 2047, 8192))

    def test_rejects_malformed_content_range(self):
        self.assertIsNone(_content_range(_Response("not a range")))


if __name__ == "__main__":
    unittest.main()
