import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
