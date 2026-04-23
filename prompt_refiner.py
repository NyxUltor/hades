from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

DEFAULT_MODEL = "gemini-1.5-flash"
DEFAULT_TAGS = ("ai", "prompt", "hades")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _slugify(value: str, max_length: int = 64) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    if not slug:
        slug = "note"
    return slug[:max_length].rstrip("-")


def refine_prompt(messy_input: str, api_key: str, model_name: str = DEFAULT_MODEL) -> str:
    if not messy_input.strip():
        raise ValueError("Input cannot be empty.")
    if not api_key:
        raise ValueError("Gemini API key is required.")

    import google.generativeai as genai

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(model_name)
    response = model.generate_content(
        "Restructure this into a clean, concise, structured prompt for a smart AI. "
        "Minimize tokens without losing intent. Use short sections and bullet points when useful.\n\n"
        f"{messy_input.strip()}"
    )
    text = (getattr(response, "text", "") or "").strip()
    if not text:
        raise RuntimeError("Gemini returned an empty response.")
    return text


def copy_to_clipboard(text: str) -> None:
    import pyperclip

    pyperclip.copy(text)


def paste_with_xdotool(text: str, window_name: str) -> None:
    subprocess.run(
        ["xdotool", "search", "--name", window_name, "windowactivate", "--sync"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    subprocess.run(["xdotool", "type", "--delay", "1", text], check=True)
    subprocess.run(["xdotool", "key", "Return"], check=True)


def paste_with_selenium(text: str, url: str) -> None:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.keys import Keys

    driver = webdriver.Firefox()
    try:
        driver.get(url)
        editor = driver.find_element(By.CSS_SELECTOR, "[contenteditable='true']")
        editor.click()
        editor.send_keys(text)
        editor.send_keys(Keys.ENTER)
    finally:
        driver.quit()


def save_refined_prompt(
    vault_path: str,
    original_input: str,
    refined_prompt: str,
    tags: Iterable[str] = (),
    now: datetime | None = None,
) -> Path:
    now = now or _utc_now()
    vault = Path(vault_path).expanduser().resolve()
    notes_dir = vault / "AI Prompts" / now.strftime("%Y") / now.strftime("%m")
    notes_dir.mkdir(parents=True, exist_ok=True)

    merged_tags = sorted({*DEFAULT_TAGS, *(t.strip() for t in tags if t.strip())})

    previous = sorted(notes_dir.glob("*.md"), key=lambda p: p.stat().st_mtime)
    related = previous[-1] if previous else None

    title_seed = " ".join(refined_prompt.split()[:8])
    filename = f"{now.strftime('%Y%m%d-%H%M%S')}-{_slugify(title_seed)}.md"
    note_path = notes_dir / filename

    with note_path.open("w", encoding="utf-8") as handle:
        handle.write("---\n")
        handle.write(f"created: {now.isoformat()}\n")
        handle.write(f"tags: [{', '.join(merged_tags)}]\n")
        handle.write("---\n\n")
        handle.write("# Refined Prompt\n\n")
        handle.write("## Original Input\n")
        handle.write(f"{original_input.strip()}\n\n")
        handle.write("## Refined Output\n")
        handle.write(f"{refined_prompt.strip()}\n\n")
        if related:
            handle.write("## Related\n")
            handle.write(f"- [[{related.stem}]]\n\n")

    timeline = vault / "AI Prompts" / "_timeline.md"
    timeline.parent.mkdir(parents=True, exist_ok=True)
    with timeline.open("a", encoding="utf-8") as handle:
        handle.write(f"- {now.strftime('%Y-%m-%d %H:%M:%S %Z')} - [[{note_path.stem}]]\n")

    return note_path


def generate_weekly_recap(vault_path: str, now: datetime | None = None) -> tuple[Path, int]:
    now = now or _utc_now()
    vault = Path(vault_path).expanduser().resolve()
    cutoff = now - timedelta(days=7)

    changed: list[Path] = []
    for note in vault.rglob("*.md"):
        if "Weekly Recaps" in note.parts:
            continue
        if note.name == "_timeline.md":
            continue
        modified = datetime.fromtimestamp(note.stat().st_mtime, timezone.utc)
        if modified >= cutoff:
            changed.append(note)

    changed.sort(key=lambda p: p.stat().st_mtime, reverse=True)

    recap_dir = vault / "Weekly Recaps"
    recap_dir.mkdir(parents=True, exist_ok=True)
    recap_path = recap_dir / f"{now.strftime('%G-W%V')}.md"

    with recap_path.open("w", encoding="utf-8") as handle:
        handle.write(f"# Weekly Recap ({now.strftime('%G-W%V')})\n\n")
        handle.write(f"Generated: {now.isoformat()}\n\n")
        handle.write(f"Updated notes in last 7 days: {len(changed)}\n\n")
        for note in changed:
            handle.write(f"- [[{note.stem}]] ({note.relative_to(vault)})\n")

    return recap_path, len(changed)


def _process_input(
    messy_input: str,
    api_key: str,
    model_name: str,
    vault_path: str,
    tags: Iterable[str],
    no_clipboard: bool,
    auto_paste: bool,
    window_name: str,
    selenium_url: str | None,
) -> None:
    refined = refine_prompt(messy_input, api_key=api_key, model_name=model_name)
    print("\nRefined Prompt:\n")
    print(refined)

    if not no_clipboard:
        copy_to_clipboard(refined)
        print("\n[Copied refined prompt to clipboard]")

    note_path = save_refined_prompt(vault_path, messy_input, refined, tags=tags)
    print(f"[Saved to Obsidian: {note_path}]")

    if auto_paste:
        try:
            paste_with_xdotool(refined, window_name)
            print("[Auto-pasted with xdotool]")
        except Exception as exc:  # noqa: BLE001
            if selenium_url:
                print(f"[xdotool failed: {exc}. Falling back to Selenium]")
                paste_with_selenium(refined, selenium_url)
                print("[Auto-pasted with Selenium fallback]")
            else:
                raise


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Hades prompt refiner and Obsidian updater.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    refine_parser = subparsers.add_parser("refine", help="Refine input and save to Obsidian.")
    refine_parser.add_argument("--input", dest="input_text", help="Messy text to refine.")
    refine_parser.add_argument("--continuous", action="store_true", help="Keep refining new input until exit.")
    refine_parser.add_argument("--vault-path", default=os.getenv("OBSIDIAN_VAULT_PATH"), help="Obsidian vault path.")
    refine_parser.add_argument("--api-key", default=os.getenv("GEMINI_API_KEY"), help="Gemini API key.")
    refine_parser.add_argument("--model", default=DEFAULT_MODEL, help="Gemini model name.")
    refine_parser.add_argument("--tags", default="", help="Comma-separated extra tags.")
    refine_parser.add_argument("--no-clipboard", action="store_true", help="Skip clipboard copy.")
    refine_parser.add_argument("--auto-paste", action="store_true", help="Paste into browser input with xdotool.")
    refine_parser.add_argument("--window-name", default="Claude|ChatGPT", help="xdotool window name pattern.")
    refine_parser.add_argument("--selenium-url", help="Optional fallback URL for Selenium paste.")

    recap_parser = subparsers.add_parser("weekly-recap", help="Generate weekly Obsidian recap.")
    recap_parser.add_argument("--vault-path", default=os.getenv("OBSIDIAN_VAULT_PATH"), help="Obsidian vault path.")

    return parser.parse_args()


def main() -> int:
    args = _parse_args()

    if not args.vault_path:
        print("Error: --vault-path or OBSIDIAN_VAULT_PATH is required.", file=sys.stderr)
        return 2

    if args.command == "weekly-recap":
        recap_path, changed = generate_weekly_recap(args.vault_path)
        print(f"Weekly recap generated: {recap_path} ({changed} updated notes)")
        return 0

    if not args.api_key:
        print("Error: --api-key or GEMINI_API_KEY is required for refine.", file=sys.stderr)
        return 2

    tags = [part.strip() for part in args.tags.split(",") if part.strip()]

    if args.continuous:
        print("Continuous mode active. Type 'exit' to quit.")
        while True:
            messy = input("\nEnter messy input: ").strip()
            if messy.lower() in {"exit", "quit"}:
                break
            if not messy:
                continue
            _process_input(
                messy,
                api_key=args.api_key,
                model_name=args.model,
                vault_path=args.vault_path,
                tags=tags,
                no_clipboard=args.no_clipboard,
                auto_paste=args.auto_paste,
                window_name=args.window_name,
                selenium_url=args.selenium_url,
            )
        return 0

    input_text = args.input_text
    if not input_text and not sys.stdin.isatty():
        input_text = sys.stdin.read().strip()
    if not input_text:
        input_text = input("Enter messy input: ").strip()

    _process_input(
        input_text,
        api_key=args.api_key,
        model_name=args.model,
        vault_path=args.vault_path,
        tags=tags,
        no_clipboard=args.no_clipboard,
        auto_paste=args.auto_paste,
        window_name=args.window_name,
        selenium_url=args.selenium_url,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
