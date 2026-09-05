import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workflow_agent.deepseek import AgentResponseFormatError, _completion_payload, _parse_json_result, _scrub, settings


class DeepSeekSafetyTests(unittest.TestCase):
    def test_scrubs_secret_and_windows_path(self):
        value = _scrub({"api_key": "sk-abcdefghijklmnopqrstuvwxyz", "model_path": r"C:\AI\models\secret.safetensors"})
        self.assertEqual(value["api_key"], "<redacted>")
        self.assertEqual(value["model_path"], "<local-path>/secret.safetensors")

    def test_parses_fenced_json(self):
        value = _parse_json_result("```json\n{\"summary\": \"正常\", \"findings\": []}\n```")
        self.assertEqual(value["summary"], "正常")
        self.assertEqual(value["findings"], [])

    def test_reports_a_safe_format_error_for_malformed_agent_json(self):
        with self.assertRaisesRegex(AgentResponseFormatError, "完整 JSON"):
            _parse_json_result('{"summary":"不完整",}')

    def test_frontend_config_is_validated_without_persistence(self):
        config = settings({"api_key": "x" * 24, "base_url": "https://api.deepseek.com", "model": "deepseek-v4-flash-vision-exp"})
        self.assertTrue(config["configured"])
        self.assertEqual(config["base_url"], "https://api.deepseek.com")
        with self.assertRaises(ValueError):
            settings({"api_key": "x" * 24, "base_url": "http://127.0.0.1:8000", "model": "test"})

    def test_tool_calls_disable_thinking_mode(self):
        payload = _completion_payload(model="deepseek-v4-flash-vision-exp", messages=[], tools=[], tool_choice="auto", temperature=0.1, max_tokens=100)
        self.assertEqual(payload["thinking"], {"type": "disabled"})
