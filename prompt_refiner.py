from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from dotenv import load_dotenv

DEFAULT_MODEL = "gemini-1.5-flash"
DEFAULT_TAGS = ("ai", "prompt", "hades")


def _load_env_config() -> tuple[str | None, str | None]:
    load_dotenv()
    obsidian_path = os.getenv("OBSIDIAN_PATH") or os.getenv("OBSIDIAN_VAULT_PATH")
    gemini_api_key = os.getenv("GEMINI_API_KEY")
    return obsidian_path, gemini_api_key


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


def _sanitize_for_xdotool(text: str) -> str:
    if "\x00" in text:
        raise ValueError("Refined prompt contains unsupported null characters.")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def paste_with_xdotool(text: str, window_name: str) -> None:
    safe_text = _sanitize_for_xdotool(text)
    subprocess.run(
        ["xdotool", "search", "--name", window_name, "windowactivate", "--sync"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    subprocess.run(["xdotool", "type", "--delay", "1", safe_text], check=True)
    subprocess.run(["xdotool", "key", "Return"], check=True)


def paste_with_selenium(text: str, url: str, browser: str = "firefox") -> None:
    from selenium import webdriver
    from selenium.common.exceptions import NoSuchElementException
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.keys import Keys

    if browser.lower() == "chrome":
        driver = webdriver.Chrome()
    else:
        driver = webdriver.Firefox()
    try:
        driver.get(url)
        try:
            editor = driver.find_element(By.CSS_SELECTOR, "[contenteditable='true']")
        except NoSuchElementException as exc:
            raise RuntimeError(
                "Could not find a rich text input field ([contenteditable='true']) on the page."
            ) from exc
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
        handle.write("tags:\n")
        for tag in merged_tags:
            handle.write(f"  - {tag}\n")
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
        handle.write(f"- {now.strftime('%Y-%m-%d %H:%M:%S')} UTC - [[{note_path.stem}]]\n")

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
    selenium_browser: str,
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
        except (FileNotFoundError, subprocess.CalledProcessError, ValueError) as exc:
            if selenium_url:
                print(f"[xdotool failed: {exc}. Falling back to Selenium]")
                paste_with_selenium(refined, selenium_url, browser=selenium_browser)
                print("[Auto-pasted with Selenium fallback]")
            else:
                raise


def _parse_args() -> argparse.Namespace:
    obsidian_path, gemini_api_key = _load_env_config()

    parser = argparse.ArgumentParser(description="Hades prompt refiner and Obsidian updater.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    refine_parser = subparsers.add_parser("refine", help="Refine input and save to Obsidian.")
    refine_parser.add_argument("--input", dest="input_text", help="Messy text to refine.")
    refine_parser.add_argument("--continuous", action="store_true", help="Keep refining new input until exit.")
    refine_parser.add_argument(
        "--vault-path",
        default=obsidian_path,
        help="Obsidian vault path.",
    )
    refine_parser.add_argument("--api-key", default=gemini_api_key, help="Gemini API key.")
    refine_parser.add_argument("--model", default=DEFAULT_MODEL, help="Gemini model name.")
    refine_parser.add_argument("--tags", default="", help="Comma-separated extra tags.")
    refine_parser.add_argument("--no-clipboard", action="store_true", help="Skip clipboard copy.")
    refine_parser.add_argument("--auto-paste", action="store_true", help="Paste into browser input with xdotool.")
    refine_parser.add_argument(
        "--window-name",
        default="Claude|ChatGPT",
        help="xdotool window title pattern (regex-style, e.g. 'Claude|ChatGPT').",
    )
    refine_parser.add_argument("--selenium-url", help="Optional fallback URL for Selenium paste.")
    refine_parser.add_argument(
        "--selenium-browser",
        default="firefox",
        choices=("firefox", "chrome"),
        help="Browser for Selenium fallback (default: firefox).",
    )

    recap_parser = subparsers.add_parser("weekly-recap", help="Generate weekly Obsidian recap.")
    recap_parser.add_argument(
        "--vault-path",
        default=obsidian_path,
        help="Obsidian vault path.",
    )

    return parser.parse_args()


def main() -> int:
    args = _parse_args()

    if not args.vault_path:
        print("Error: --vault-path or OBSIDIAN_PATH is required.", file=sys.stderr)
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
                selenium_browser=args.selenium_browser,
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
        selenium_browser=args.selenium_browser,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
