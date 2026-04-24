import json
import os
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, call, patch

import prompt_refiner


class PromptRefinerStorageTests(unittest.TestCase):
    def test_save_to_daily_log_appends_to_daily_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            now = datetime(2026, 4, 23, 10, 0, tzinfo=timezone.utc)
            log1 = prompt_refiner.save_to_daily_log(
                tmpdir,
                original_input="messy one",
                refined_prompt="Clean prompt one",
                now=now,
            )
            log2 = prompt_refiner.save_to_daily_log(
                tmpdir,
                original_input="messy two",
                refined_prompt="Clean prompt two",
                now=now + timedelta(seconds=60),
            )

            # Both calls return the same daily log file.
            self.assertEqual(log1, log2)
            self.assertTrue(log1.exists())

            log_text = log1.read_text(encoding="utf-8")
            self.assertIn("messy one", log_text)
            self.assertIn("Clean prompt one", log_text)
            self.assertIn("messy two", log_text)
            self.assertIn("Clean prompt two", log_text)

            timeline = Path(tmpdir) / "AI Prompts" / "_timeline.md"
            self.assertTrue(timeline.exists())
            timeline_text = timeline.read_text(encoding="utf-8")
            self.assertIn("2026-04-23", timeline_text)

    def test_save_to_daily_log_writes_header_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            now = datetime(2026, 4, 23, 10, 0, tzinfo=timezone.utc)
            for i in range(3):
                prompt_refiner.save_to_daily_log(
                    tmpdir,
                    original_input=f"input {i}",
                    refined_prompt=f"refined {i}",
                    now=now + timedelta(minutes=i),
                )

            log_file = Path(tmpdir) / "AI Prompts" / "2026-04-23.md"
            log_text = log_file.read_text(encoding="utf-8")
            # Header should appear exactly once.
            self.assertEqual(log_text.count("# 2026-04-23 Prompt Log"), 1)

    def test_generate_weekly_recap_only_counts_recent_daily_logs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir) / "AI Prompts"
            log_dir.mkdir(parents=True)

            old_log = log_dir / "2026-04-01.md"
            old_log.write_text(
                "# 2026-04-01 Prompt Log\n\n"
                "### 12:00:00 UTC\n\n**Original:**\nold\n\n**Refined:**\nold refined\n\n---\n\n",
                encoding="utf-8",
            )

            recent_log = log_dir / "2026-04-22.md"
            recent_log.write_text(
                "# 2026-04-22 Prompt Log\n\n"
                "### 12:00:00 UTC\n\n**Original:**\nrecent\n\n**Refined:**\nrecent refined\n\n---\n\n",
                encoding="utf-8",
            )

            recap_path, changed = prompt_refiner.generate_weekly_recap(
                tmpdir,
                now=datetime(2026, 4, 23, 12, 0, tzinfo=timezone.utc),
            )

            self.assertEqual(changed, 1)
            self.assertTrue(recap_path.exists())
            recap_text = recap_path.read_text(encoding="utf-8")
            self.assertIn("2026-04-22", recap_text)
            self.assertNotIn("2026-04-01", recap_text)


class PromptRefinerUnitTests(unittest.TestCase):
    def test_parse_args_uses_obsidian_path_and_api_key_defaults(self) -> None:
        with patch.dict(
            os.environ,
            {"OBSIDIAN_PATH": "/tmp/vault", "GEMINI_API_KEY": "abc", "OBSIDIAN_VAULT_PATH": ""},
            clear=False,
        ), patch.object(sys, "argv", ["prompt_refiner.py", "refine", "--input", "messy"]):
            args = prompt_refiner._parse_args()  # noqa: SLF001

        self.assertEqual(args.vault_path, "/tmp/vault")
        self.assertEqual(args.api_key, "abc")

    def test_parse_args_keeps_legacy_obsidian_vault_path_fallback(self) -> None:
        with patch.dict(
            os.environ,
            {"OBSIDIAN_PATH": "", "OBSIDIAN_VAULT_PATH": "/tmp/legacy-vault"},
            clear=False,
        ), patch.object(sys, "argv", ["prompt_refiner.py", "weekly-recap"]):
            args = prompt_refiner._parse_args()  # noqa: SLF001

        self.assertEqual(args.vault_path, "/tmp/legacy-vault")

    def test_parse_args_includes_no_context_flag(self) -> None:
        with patch.dict(
            os.environ,
            {"OBSIDIAN_PATH": "/tmp/vault", "GEMINI_API_KEY": "abc"},
            clear=False,
        ), patch.object(sys, "argv", ["prompt_refiner.py", "refine", "--input", "test", "--no-context"]):
            args = prompt_refiner._parse_args()  # noqa: SLF001

        self.assertTrue(args.no_context)

    def test_refine_prompt_validates_input_and_api_key(self) -> None:
        with self.assertRaises(ValueError):
            prompt_refiner.refine_prompt("", api_key="k")
        with self.assertRaises(ValueError):
            prompt_refiner.refine_prompt("hi", api_key="")

    def test_refine_prompt_uses_genai_client_with_auto_model_selection(self) -> None:
        fake_response = types.SimpleNamespace(text="Refined output")
        fake_client = Mock()
        fake_client.models.list.return_value = [
            types.SimpleNamespace(name="models/gemini-2.5-flash", supported_actions=["generateContent"])
        ]
        fake_client.models.generate_content.return_value = fake_response
        fake_config = Mock()
        fake_genai = types.SimpleNamespace(
            Client=Mock(return_value=fake_client),
            types=types.SimpleNamespace(
                GenerateContentConfig=Mock(return_value=fake_config),
            ),
        )
        fake_google = types.SimpleNamespace(genai=fake_genai)
        with patch.dict("sys.modules", {"google": fake_google, "google.genai": fake_genai}):
            refined = prompt_refiner.refine_prompt("messy input", api_key="abc")

        self.assertEqual(refined, "Refined output")
        fake_genai.Client.assert_called_once_with(api_key="abc")
        fake_client.models.list.assert_called_once_with()
        fake_client.models.generate_content.assert_called_once()
        self.assertEqual(fake_client.models.generate_content.call_args.kwargs["model"], "models/gemini-2.5-flash")
        # Verify system instruction was applied.
        fake_genai.types.GenerateContentConfig.assert_called_once()
        cfg_kwargs = fake_genai.types.GenerateContentConfig.call_args.kwargs
        self.assertIn("system_instruction", cfg_kwargs)
        self.assertEqual(cfg_kwargs["system_instruction"], prompt_refiner.REFINE_SYSTEM_PROMPT)

    def test_refine_prompt_raises_when_no_supported_model_is_available(self) -> None:
        fake_client = Mock()
        fake_client.models.list.return_value = [types.SimpleNamespace(name="models/embedding-001", supported_actions=[])]
        fake_genai = types.SimpleNamespace(
            Client=Mock(return_value=fake_client),
        )
        fake_google = types.SimpleNamespace(genai=fake_genai)
        with patch.dict("sys.modules", {"google": fake_google, "google.genai": fake_genai}):
            with self.assertRaises(RuntimeError):
                prompt_refiner.refine_prompt("messy input", api_key="abc")

    def test_refine_prompt_falls_back_to_ollama_on_gemini_error(self) -> None:
        fake_client = Mock()
        fake_client.models.list.return_value = [
            types.SimpleNamespace(name="models/gemini-1.5-flash", supported_actions=["generateContent"])
        ]
        fake_client.models.generate_content.side_effect = RuntimeError("503 Service Unavailable")
        fake_config = Mock()
        fake_genai = types.SimpleNamespace(
            Client=Mock(return_value=fake_client),
            types=types.SimpleNamespace(GenerateContentConfig=Mock(return_value=fake_config)),
        )
        fake_google = types.SimpleNamespace(genai=fake_genai)
        ollama_cfg = {"enabled": "true", "model": "gemma3:1b", "url": "http://localhost:11434"}

        with patch("prompt_refiner.refine_with_ollama", return_value="Ollama output") as mock_ollama, patch.dict(
            "sys.modules", {"google": fake_google, "google.genai": fake_genai}
        ):
            result = prompt_refiner.refine_prompt("messy input", api_key="abc", ollama_cfg=ollama_cfg)

        self.assertEqual(result, "Ollama output")
        mock_ollama.assert_called_once()

    def test_select_generation_model_prefers_stable_models(self) -> None:
        fake_client = Mock()
        fake_client.models.list.return_value = [
            types.SimpleNamespace(name="models/gemini-2.5-flash-preview", supported_actions=["generateContent"]),
            types.SimpleNamespace(name="models/gemini-1.5-flash", supported_actions=["generateContent"]),
            types.SimpleNamespace(name="models/gemini-2.0-flash", supported_actions=["generateContent"]),
        ]
        with patch.dict(os.environ, {"GEMINI_MODEL": ""}):
            result = prompt_refiner._select_generation_model(fake_client)  # noqa: SLF001
        # gemini-2.0-flash is first in the preferred list.
        self.assertEqual(result, "models/gemini-2.0-flash")

    def test_select_generation_model_honours_env_override(self) -> None:
        fake_client = Mock()
        fake_client.models.list.return_value = [
            types.SimpleNamespace(name="models/gemini-1.5-flash", supported_actions=["generateContent"]),
        ]
        with patch.dict(os.environ, {"GEMINI_MODEL": "models/gemini-custom"}):
            result = prompt_refiner._select_generation_model(fake_client)  # noqa: SLF001
        self.assertEqual(result, "models/gemini-custom")

    def test_refine_with_ollama_calls_local_api(self) -> None:
        mock_response_data = json.dumps({"response": "Refined by Ollama"}).encode()
        mock_resp = Mock()
        mock_resp.read.return_value = mock_response_data
        mock_resp.__enter__ = Mock(return_value=mock_resp)
        mock_resp.__exit__ = Mock(return_value=False)

        with patch("prompt_refiner.urllib.request.urlopen", return_value=mock_resp):
            result = prompt_refiner.refine_with_ollama("messy input", "http://localhost:11434", "gemma3:1b")

        self.assertEqual(result, "Refined by Ollama")

    def test_read_recent_context_returns_none_when_no_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            now = datetime(2026, 4, 23, 10, 0, tzinfo=timezone.utc)
            result = prompt_refiner._read_recent_context(tmpdir, now)  # noqa: SLF001
        self.assertIsNone(result)

    def test_read_recent_context_returns_recent_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            now = datetime(2026, 4, 23, 10, 0, tzinfo=timezone.utc)
            log_dir = Path(tmpdir) / "AI Prompts"
            log_dir.mkdir()
            log_file = log_dir / "2026-04-23.md"
            log_file.write_text(
                "# 2026-04-23 Prompt Log\n\n"
                "### 08:00:00 UTC\n\n**Original:**\nfirst\n\n**Refined:**\nfirst refined\n\n---\n\n"
                "### 09:00:00 UTC\n\n**Original:**\nsecond\n\n**Refined:**\nsecond refined\n\n---\n\n",
                encoding="utf-8",
            )
            result = prompt_refiner._read_recent_context(tmpdir, now)  # noqa: SLF001

        self.assertIsNotNone(result)
        self.assertIn("first refined", result)
        self.assertIn("second refined", result)

    def test_copy_to_clipboard_calls_pyperclip(self) -> None:
        fake_pyperclip = types.SimpleNamespace(copy=Mock())
        with patch.dict("sys.modules", {"pyperclip": fake_pyperclip}):
            prompt_refiner.copy_to_clipboard("hello")
        fake_pyperclip.copy.assert_called_once_with("hello")

    def test_paste_with_xdotool_runs_expected_commands(self) -> None:
        with patch("prompt_refiner.subprocess.run") as run:
            prompt_refiner.paste_with_xdotool("hello", "Claude")

        self.assertEqual(run.call_count, 3)
        run.assert_has_calls(
            [
                call(
                    ["xdotool", "search", "--name", "Claude", "windowactivate", "--sync"],
                    check=True,
                    stdout=prompt_refiner.subprocess.PIPE,
                    stderr=prompt_refiner.subprocess.PIPE,
                    text=True,
                ),
                call(["xdotool", "type", "--delay", "1", "hello"], check=True),
                call(["xdotool", "key", "Return"], check=True),
            ]
        )

    def test_paste_with_xdotool_rejects_null_characters(self) -> None:
        with patch("prompt_refiner.subprocess.run") as run:
            with self.assertRaises(ValueError):
                prompt_refiner.paste_with_xdotool("bad\x00text", "Claude")
        run.assert_not_called()

    def test_process_input_falls_back_to_selenium_when_xdotool_fails(self) -> None:
        with patch("prompt_refiner.refine_prompt", return_value="refined"), patch(
            "prompt_refiner.copy_to_clipboard"
        ), patch(
            "prompt_refiner.save_to_daily_log", return_value=Path("/tmp/test-note.md")
        ), patch(
            "prompt_refiner.paste_with_xdotool", side_effect=FileNotFoundError("xdotool missing")
        ), patch(
            "prompt_refiner.paste_with_selenium"
        ) as selenium_fallback:
            prompt_refiner._process_input(  # noqa: SLF001
                "messy",
                api_key="abc",
                vault_path="/tmp/vault",
                tags=[],
                no_clipboard=False,
                auto_paste=True,
                window_name="Claude",
                selenium_url="https://chatgpt.com",
                selenium_browser="firefox",
            )

        selenium_fallback.assert_called_once_with("refined", "https://chatgpt.com", browser="firefox")


if __name__ == "__main__":
    unittest.main()
