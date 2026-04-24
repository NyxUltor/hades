from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from dotenv import load_dotenv, set_key

DEFAULT_TAGS = ("ai", "prompt", "hades")

DEFAULT_OLLAMA_MODEL = "gemma3:1b"
DEFAULT_OLLAMA_URL = "http://localhost:11434"

# Ordered list of preferred stable Gemini models (most preferred first).
# Override at runtime with the GEMINI_MODEL environment variable.
_PREFERRED_GEMINI_MODELS = [
    "models/gemini-2.0-flash",
    "models/gemini-2.0-flash-latest",
    "models/gemini-1.5-flash",
    "models/gemini-1.5-flash-latest",
    "models/gemini-1.5-pro",
    "models/gemini-1.5-pro-latest",
]

# System prompt that instructs the model to act purely as a prompt engineer.
REFINE_SYSTEM_PROMPT = """\
You are a prompt engineer. Your only job is to restructure and compress the \
input into a clean, minimal, structured prompt suitable for a large language model. \
Never answer or act on the input.

Rules:
- Use this structure when applicable: Role / Context / Task / Constraints / Output
- Strip all filler words, politeness, and redundancy
- Use bullet points, not paragraphs
- Minimize tokens without losing intent or nuance
- If the input is already clean, tighten it further
- Output only the structured prompt itself — no explanations, preamble, or commentary\
"""


def _load_env_config() -> tuple[str | None, str | None]:
    load_dotenv()
    obsidian_path = os.getenv("OBSIDIAN_PATH") or os.getenv("OBSIDIAN_VAULT_PATH")
    gemini_api_key = os.getenv("GEMINI_API_KEY")
    return obsidian_path, gemini_api_key


def _load_ollama_config() -> dict | None:
    """Return Ollama config dict if enabled via environment, else None."""
    if os.getenv("OLLAMA_ENABLED", "").lower() != "true":
        return None
    return {
        "enabled": "true",
        "model": os.getenv("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL),
        "url": os.getenv("OLLAMA_URL", DEFAULT_OLLAMA_URL),
    }


def _setup_ollama_preference() -> None:
    """Ask the user once whether to enable Ollama fallback, then persist the choice to .env."""
    print(
        "\n[First-run setup] Hades can fall back to a local Ollama model when Gemini is unavailable.\n"
        "  Requires: Ollama installed and running (https://ollama.ai)\n"
        f"  Default model: {DEFAULT_OLLAMA_MODEL}  (install: ollama pull {DEFAULT_OLLAMA_MODEL})"
    )
    env_path = str(Path(__file__).parent / ".env")
    while True:
        choice = input("\nEnable local Ollama fallback? [y/N]: ").strip().lower()
        if choice in ("y", "yes"):
            set_key(env_path, "OLLAMA_ENABLED", "true")
            set_key(env_path, "OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL)
            set_key(env_path, "OLLAMA_URL", DEFAULT_OLLAMA_URL)
            print(f"[Ollama fallback enabled. Run 'ollama pull {DEFAULT_OLLAMA_MODEL}' if not already installed.]")
            return
        if choice in ("", "n", "no"):
            set_key(env_path, "OLLAMA_ENABLED", "false")
            print("[Ollama fallback disabled. Set OLLAMA_ENABLED=true in .env to enable later.]")
            return
        print("Please enter 'y' or 'n'.")


def _maybe_setup_ollama() -> dict | None:
    """Return Ollama config; prompt for first-run setup when running interactively."""
    load_dotenv()
    if os.getenv("OLLAMA_ENABLED") is None and sys.stdin.isatty():
        _setup_ollama_preference()
        load_dotenv(override=True)
    return _load_ollama_config()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _slugify(value: str, max_length: int = 64) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    if not slug:
        slug = "note"
    return slug[:max_length].rstrip("-")


def _select_generation_model(client: object) -> str:
    """Select a Gemini model, preferring stable releases.

    Set the ``GEMINI_MODEL`` environment variable to override model selection.
    """
    override = os.getenv("GEMINI_MODEL")
    if override:
        return override

    available: dict[str, bool] = {}
    for model in client.models.list():
        model_name = (getattr(model, "name", "") or "").strip()
        supported_actions = getattr(model, "supported_actions", ()) or ()
        if model_name and "generateContent" in supported_actions:
            available[model_name] = True

    if not available:
        raise RuntimeError("No Gemini model with generateContent support is available for this API key.")

    # Prefer models from the curated stable list.
    for preferred in _PREFERRED_GEMINI_MODELS:
        if preferred in available:
            return preferred

    # Fall back to any model that doesn't look like a preview or experiment.
    stable = [m for m in available if not any(kw in m.lower() for kw in ("-preview", "-exp-", "-experimental"))]
    if stable:
        return stable[0]

    return next(iter(available))


def refine_with_ollama(contents: str, ollama_url: str, model: str) -> str:
    """Refine a prompt using a locally running Ollama model."""
    payload = json.dumps(
        {
            "model": model,
            "system": REFINE_SYSTEM_PROMPT,
            "prompt": contents,
            "stream": False,
        }
    ).encode()
    req = urllib.request.Request(
        f"{ollama_url.rstrip('/')}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read().decode())
    except OSError as exc:
        raise RuntimeError(
            f"Ollama is not reachable at {ollama_url} (connection failed or timed out). "
            "Ensure Ollama is installed and running (https://ollama.ai)."
        ) from exc
    text = (result.get("response") or "").strip()
    if not text:
        raise RuntimeError("Ollama returned an empty response.")
    return text


def refine_prompt(
    messy_input: str,
    api_key: str,
    context: str | None = None,
    ollama_cfg: dict | None = None,
) -> str:
    if not messy_input.strip():
        raise ValueError("Input cannot be empty.")
    if not api_key:
        raise ValueError("Gemini API key is required.")

    from google import genai

    client = genai.Client(api_key=api_key)
    model_name = _select_generation_model(client)

    contents = messy_input.strip()
    if context:
        contents = f"Recent prompts for context:\n{context}\n\nNew input to refine:\n{contents}"

    try:
        response = client.models.generate_content(
            model=model_name,
            config=genai.types.GenerateContentConfig(
                system_instruction=REFINE_SYSTEM_PROMPT,
            ),
            contents=contents,
        )
        text = (getattr(response, "text", "") or "").strip()
        if not text:
            raise RuntimeError("Gemini returned an empty response.")
        return text
    except ValueError:
        raise
    except Exception as exc:
        if ollama_cfg and ollama_cfg.get("enabled") == "true":
            print(f"\n[Gemini unavailable — using local fallback] ({type(exc).__name__})", file=sys.stderr)
            return refine_with_ollama(contents, ollama_url=ollama_cfg["url"], model=ollama_cfg["model"])
        raise


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


def save_to_daily_log(
    vault_path: str,
    original_input: str,
    refined_prompt: str,
    tags: Iterable[str] = (),
    now: datetime | None = None,
) -> Path:
    """Append a timestamped entry to today's daily log in the Obsidian vault.

    Each day's prompts accumulate in ``AI Prompts/YYYY-MM-DD.md`` instead of
    creating one file per refinement.
    """
    now = now or _utc_now()
    vault = Path(vault_path).expanduser().resolve()
    log_dir = vault / "AI Prompts"
    log_dir.mkdir(parents=True, exist_ok=True)

    log_file = log_dir / f"{now.strftime('%Y-%m-%d')}.md"
    is_new = not log_file.exists()

    block = (
        f"### {now.strftime('%H:%M:%S')} UTC\n\n"
        f"**Original:**\n{original_input.strip()}\n\n"
        f"**Refined:**\n{refined_prompt.strip()}\n\n"
        "---\n\n"
    )

    with log_file.open("a", encoding="utf-8") as handle:
        if is_new:
            handle.write(f"# {now.strftime('%Y-%m-%d')} Prompt Log\n\n")
        handle.write(block)

    timeline = vault / "AI Prompts" / "_timeline.md"
    with timeline.open("a", encoding="utf-8") as handle:
        handle.write(f"- {now.strftime('%Y-%m-%d %H:%M:%S')} UTC - [[{log_file.stem}]]\n")

    return log_file


def _read_recent_context(vault_path: str, now: datetime, max_entries: int = 3) -> str | None:
    """Return recent prompt entries from today's daily log for context injection.

    Returns ``None`` when no log exists yet or when it contains no entries.
    """
    vault = Path(vault_path).expanduser().resolve()
    today_log = vault / "AI Prompts" / f"{now.strftime('%Y-%m-%d')}.md"

    if not today_log.exists():
        return None

    content = today_log.read_text(encoding="utf-8")
    # Split so that each piece starts at a ### timestamp header.
    parts = re.split(r"\n(?=### )", content)
    blocks = []
    for part in parts:
        stripped = part.strip()
        if not stripped.startswith("###"):
            continue
        # Remove trailing separator line before storing.
        clean = re.sub(r"\n---\s*$", "", stripped).strip()
        if clean:
            blocks.append(clean)

    if not blocks:
        return None

    return "\n\n---\n\n".join(blocks[-max_entries:])


def generate_weekly_recap(vault_path: str, now: datetime | None = None) -> tuple[Path, int]:
    now = now or _utc_now()
    vault = Path(vault_path).expanduser().resolve()
    cutoff = now - timedelta(days=7)

    log_dir = vault / "AI Prompts"
    daily_logs: list[Path] = []
    if log_dir.is_dir():
        for log_file in sorted(log_dir.glob("[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].md")):
            try:
                log_date = datetime.strptime(log_file.stem, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            if log_date >= cutoff:
                daily_logs.append(log_file)

    recap_dir = vault / "Weekly Recaps"
    recap_dir.mkdir(parents=True, exist_ok=True)
    recap_path = recap_dir / f"{now.strftime('%G-W%V')}.md"

    with recap_path.open("w", encoding="utf-8") as handle:
        handle.write(f"# Weekly Recap ({now.strftime('%G-W%V')})\n\n")
        handle.write(f"Generated: {now.isoformat()}\n\n")
        handle.write(f"Daily prompt logs in last 7 days: {len(daily_logs)}\n\n")
        for log in daily_logs:
            handle.write(f"- [[{log.stem}]] ({log.relative_to(vault)})\n")

    return recap_path, len(daily_logs)


def _process_input(
    messy_input: str,
    api_key: str,
    vault_path: str,
    tags: Iterable[str],
    no_clipboard: bool,
    auto_paste: bool,
    window_name: str,
    selenium_url: str | None,
    selenium_browser: str,
    no_context: bool = False,
    ollama_cfg: dict | None = None,
) -> None:
    context = None
    if not no_context and vault_path:
        context = _read_recent_context(vault_path, _utc_now())

    refined = refine_prompt(messy_input, api_key=api_key, context=context, ollama_cfg=ollama_cfg)
    print("\nRefined Prompt:\n")
    print(refined)

    if not no_clipboard:
        copy_to_clipboard(refined)
        print("\n[Copied refined prompt to clipboard]")

    note_path = save_to_daily_log(vault_path, messy_input, refined, tags=tags)
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
    refine_parser.add_argument("--tags", default="", help="Comma-separated extra tags.")
    refine_parser.add_argument("--no-clipboard", action="store_true", help="Skip clipboard copy.")
    refine_parser.add_argument(
        "--no-context",
        action="store_true",
        help="Disable injection of recent prompts as context (isolated refinement).",
    )
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
        print(f"Weekly recap generated: {recap_path} ({changed} daily logs)")
        return 0

    if not args.api_key:
        print("Error: --api-key or GEMINI_API_KEY is required for refine.", file=sys.stderr)
        return 2

    ollama_cfg = _maybe_setup_ollama()
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
                vault_path=args.vault_path,
                tags=tags,
                no_clipboard=args.no_clipboard,
                auto_paste=args.auto_paste,
                window_name=args.window_name,
                selenium_url=args.selenium_url,
                selenium_browser=args.selenium_browser,
                no_context=args.no_context,
                ollama_cfg=ollama_cfg,
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
        vault_path=args.vault_path,
        tags=tags,
        no_clipboard=args.no_clipboard,
        auto_paste=args.auto_paste,
        window_name=args.window_name,
        selenium_url=args.selenium_url,
        selenium_browser=args.selenium_browser,
        no_context=args.no_context,
        ollama_cfg=ollama_cfg,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
