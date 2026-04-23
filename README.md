# hades

Hades is a lightweight AI prompt refiner + Obsidian knowledge pipeline.

## Features

- Refines messy input with Gemini **`gemini-1.5-flash`** using the correct `GenerativeModel` API usage.
- Copies refined output to clipboard automatically.
- Optionally pastes into Claude/ChatGPT browser windows using **xdotool** (Linux-first).
- Optional Selenium fallback for browser automation.
- Saves all refined prompts into an Obsidian vault with timestamp metadata and tags.
- Maintains a timeline note that keeps growing as new prompts are generated.
- Generates weekly recap notes for Obsidian changes from the last 7 days.

## Install

```bash
python -m pip install -r requirements.txt
```

Optional fallback dependency:

```bash
python -m pip install selenium
```

Install xdotool on Linux:

```bash
sudo apt-get install xdotool
```

## Configuration

Create a `.env` file in the project root:

```bash
cp .env.example .env
```

Then set:

```bash
OBSIDIAN_PATH=/absolute/path/to/your/ObsidianVault
GEMINI_API_KEY=your-key
```

`prompt_refiner.py` loads these values via `python-dotenv` automatically.  
(`OBSIDIAN_VAULT_PATH` is still accepted as a fallback for compatibility.)

## Usage

### 1) Refine one prompt

```bash
python prompt_refiner.py refine --input "clean this rough idea into a sharp prompt"
```

### 2) Refine continuously (keeps auto-adding knowledge over time)

```bash
python prompt_refiner.py refine --continuous
```

### 3) Auto-paste with xdotool

```bash
python prompt_refiner.py refine --input "..." --auto-paste --window-name "Claude|ChatGPT"
```

### 4) Selenium fallback (optional)

```bash
python prompt_refiner.py refine --input "..." --auto-paste --selenium-url "https://chatgpt.com"
```

### 5) Weekly recap generation

```bash
python prompt_refiner.py weekly-recap
```

## Weekly automation

Run weekly recap automatically with cron:

```bash
crontab -e
# Every Sunday at 01:00
0 1 * * 0 cd /path/to/hades && /usr/bin/python3 prompt_refiner.py weekly-recap
```

This gives you:

- Continuous Obsidian growth as prompts are refined.
- Automatic weekly recap updates.
