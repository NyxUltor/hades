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
    def test_save_refined_prompt_creates_note_and_related_link(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            now = datetime(2026, 4, 23, 10, 0, tzinfo=timezone.utc)
            first = prompt_refiner.save_refined_prompt(
                tmpdir,
                original_input="messy one",
                refined_prompt="Clean prompt one",
                now=now,
            )
            second = prompt_refiner.save_refined_prompt(
                tmpdir,
                original_input="messy two",
                refined_prompt="Clean prompt two",
                now=now + timedelta(seconds=1),
            )

            self.assertTrue(first.exists())
            self.assertTrue(second.exists())

            second_text = second.read_text(encoding="utf-8")
            self.assertIn("## Related", second_text)
            self.assertIn(first.stem, second_text)

            timeline = Path(tmpdir) / "AI Prompts" / "_timeline.md"
            self.assertTrue(timeline.exists())
            timeline_text = timeline.read_text(encoding="utf-8")
            self.assertIn(first.stem, timeline_text)
            self.assertIn(second.stem, timeline_text)

    def test_generate_weekly_recap_only_counts_recent_notes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            old_note = base / "old.md"
            old_note.write_text("old", encoding="utf-8")
            old_ts = datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc).timestamp()
            os.utime(old_note, (old_ts, old_ts))

            recent_note = base / "recent.md"
            recent_note.write_text("recent", encoding="utf-8")
            recent_ts = datetime(2026, 4, 22, 12, 0, tzinfo=timezone.utc).timestamp()
            os.utime(recent_note, (recent_ts, recent_ts))

            recap_path, changed = prompt_refiner.generate_weekly_recap(
                tmpdir,
                now=datetime(2026, 4, 23, 12, 0, tzinfo=timezone.utc),
            )

            self.assertEqual(changed, 1)
            self.assertTrue(recap_path.exists())
            recap_text = recap_path.read_text(encoding="utf-8")
            self.assertIn("recent.md", recap_text)
            self.assertNotIn("old.md", recap_text)


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

    def test_refine_prompt_validates_input_and_api_key(self) -> None:
        with self.assertRaises(ValueError):
            prompt_refiner.refine_prompt("", api_key="k")
        with self.assertRaises(ValueError):
            prompt_refiner.refine_prompt("hi", api_key="")

    def test_refine_prompt_uses_generative_model_api(self) -> None:
        fake_response = types.SimpleNamespace(text="Refined output")
        fake_model = Mock()
        fake_model.generate_content.return_value = fake_response
        fake_genai = types.SimpleNamespace(
            configure=Mock(),
            GenerativeModel=Mock(return_value=fake_model),
        )
        fake_google = types.SimpleNamespace(generativeai=fake_genai)
        with patch.dict("sys.modules", {"google": fake_google, "google.generativeai": fake_genai}):
            refined = prompt_refiner.refine_prompt("messy input", api_key="abc")

        self.assertEqual(refined, "Refined output")
        fake_genai.configure.assert_called_once_with(api_key="abc")
        fake_genai.GenerativeModel.assert_called_once_with("gemini-1.5-flash")
        fake_model.generate_content.assert_called_once()

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
            "prompt_refiner.save_refined_prompt", return_value=Path("/tmp/test-note.md")
        ), patch(
            "prompt_refiner.paste_with_xdotool", side_effect=FileNotFoundError("xdotool missing")
        ), patch(
            "prompt_refiner.paste_with_selenium"
        ) as selenium_fallback:
            prompt_refiner._process_input(  # noqa: SLF001
                "messy",
                api_key="abc",
                model_name="gemini-1.5-flash",
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
