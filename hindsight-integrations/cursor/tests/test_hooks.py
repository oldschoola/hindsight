"""Tests for Cursor plugin hook scripts (recall and retain)."""

import importlib
import io
import json
import os
import sys
import tempfile
from unittest.mock import MagicMock, patch

import pytest

# Import the hook scripts as modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))


class TestRecallHook:
    def test_skips_when_auto_recall_disabled(self, monkeypatch, capsys):
        monkeypatch.setenv("CURSOR_PLUGIN_ROOT", "/nonexistent")
        monkeypatch.setenv("HINDSIGHT_AUTO_RECALL", "false")
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"prompt": "test"})))

        import recall
        importlib.reload(recall)
        recall.main()

        output = capsys.readouterr()
        assert output.out == ""  # No JSON output means no context injected

    def test_skips_short_prompt(self, monkeypatch, capsys):
        monkeypatch.setenv("CURSOR_PLUGIN_ROOT", "/nonexistent")
        monkeypatch.setenv("HINDSIGHT_API_URL", "http://localhost:8888")
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"prompt": "hi"})))

        import recall
        importlib.reload(recall)
        recall.main()

        output = capsys.readouterr()
        assert output.out == ""

    def test_outputs_context_on_results(self, monkeypatch, capsys):
        monkeypatch.setenv("CURSOR_PLUGIN_ROOT", "/nonexistent")

        mock_client = MagicMock()
        mock_client.recall.return_value = {
            "results": [{"text": "User prefers TypeScript", "type": "world", "mentioned_at": "2026-01-01"}]
        }

        hook_input = {"prompt": "What language should I use?", "cwd": "/tmp/test"}
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(hook_input)))

        import recall
        importlib.reload(recall)

        with patch.object(recall, "get_api_url", return_value="http://localhost:8888"), \
             patch.object(recall, "HindsightClient", return_value=mock_client), \
             patch.object(recall, "ensure_bank_mission"), \
             patch.object(recall, "write_state"):
            recall.main()

        output = capsys.readouterr()
        result = json.loads(output.out)
        assert "additionalContext" in result["hookSpecificOutput"]
        assert "User prefers TypeScript" in result["hookSpecificOutput"]["additionalContext"]
        assert "hindsight_memories" in result["hookSpecificOutput"]["additionalContext"]

    def test_no_output_on_empty_results(self, monkeypatch, capsys):
        monkeypatch.setenv("CURSOR_PLUGIN_ROOT", "/nonexistent")

        mock_client = MagicMock()
        mock_client.recall.return_value = {"results": []}

        hook_input = {"prompt": "Tell me about quantum physics"}
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(hook_input)))

        import recall
        importlib.reload(recall)

        with patch.object(recall, "get_api_url", return_value="http://localhost:8888"), \
             patch.object(recall, "HindsightClient", return_value=mock_client), \
             patch.object(recall, "ensure_bank_mission"):
            recall.main()

        output = capsys.readouterr()
        assert output.out == ""


class TestRetainHook:
    def test_skips_when_auto_retain_disabled(self, monkeypatch, capsys):
        monkeypatch.setenv("CURSOR_PLUGIN_ROOT", "/nonexistent")
        monkeypatch.setenv("HINDSIGHT_AUTO_RETAIN", "false")
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"conversation_id": "c1"})))

        import retain
        importlib.reload(retain)
        retain.main()

        output = capsys.readouterr()
        assert output.out == ""

    def test_skips_empty_transcript(self, monkeypatch, capsys):
        monkeypatch.setenv("CURSOR_PLUGIN_ROOT", "/nonexistent")
        monkeypatch.setenv("HINDSIGHT_API_URL", "http://localhost:8888")

        hook_input = {"conversation_id": "c1", "transcript_path": "/nonexistent/transcript.jsonl"}
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(hook_input)))

        import retain
        importlib.reload(retain)
        retain.main()

    def test_retains_transcript(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CURSOR_PLUGIN_ROOT", "/nonexistent")
        monkeypatch.setenv("HINDSIGHT_RETAIN_EVERY_N_TURNS", "1")

        mock_client = MagicMock()
        mock_client.retain.return_value = {"status": "ok"}

        # Write a test transcript
        transcript_path = tmp_path / "transcript.jsonl"
        messages = [
            {"role": "user", "content": "Build a React app"},
            {"role": "assistant", "content": "I'll create a React app for you."},
        ]
        transcript_path.write_text("\n".join(json.dumps(m) for m in messages))

        hook_input = {
            "conversation_id": "conv-123",
            "transcript_path": str(transcript_path),
            "cwd": "/tmp/test",
        }
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(hook_input)))
        monkeypatch.setenv("CURSOR_PLUGIN_DATA", str(tmp_path / "data"))

        import retain
        importlib.reload(retain)

        with patch.object(retain, "get_api_url", return_value="http://localhost:8888"), \
             patch.object(retain, "HindsightClient", return_value=mock_client), \
             patch.object(retain, "ensure_bank_mission"):
            retain.main()

        mock_client.retain.assert_called_once()
        call_kwargs = mock_client.retain.call_args
        assert "bank_id" in call_kwargs[1]
        assert call_kwargs[1]["context"] == "cursor"


class TestManifest:
    def test_plugin_json_valid(self):
        plugin_path = os.path.join(
            os.path.dirname(__file__), "..", ".cursor-plugin", "plugin.json"
        )
        with open(plugin_path) as f:
            manifest = json.load(f)

        assert manifest["name"] == "hindsight-memory"
        assert "description" in manifest
        assert manifest["version"]
        assert manifest["license"] == "MIT"

    def test_hooks_json_valid(self):
        hooks_path = os.path.join(
            os.path.dirname(__file__), "..", "hooks", "hooks.json"
        )
        with open(hooks_path) as f:
            hooks = json.load(f)

        assert hooks["version"] == 1
        assert "beforeSubmitPrompt" in hooks["hooks"]
        assert "stop" in hooks["hooks"]

    def test_settings_json_valid(self):
        settings_path = os.path.join(
            os.path.dirname(__file__), "..", "settings.json"
        )
        with open(settings_path) as f:
            settings = json.load(f)

        assert settings["bankId"] == "cursor"
        assert settings["retainContext"] == "cursor"
        assert settings["agentName"] == "cursor"
        assert "autoRecall" in settings
        assert "autoRetain" in settings
