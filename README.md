# hades

Hades is a lightweight AI prompt refiner + Obsidian knowledge pipeline.

## Features

- Refines messy input with Gemini via the modern **`google.genai`** client API.
- Uses a strong prompt-engineering system prompt: restructures input into Role / Context / Task / Constraints / Output format.
- Selects a compatible, **stable** generation model automatically at runtime (override via `GEMINI_MODEL`).
- **Falls back to a local Ollama model** when Gemini returns 429/503 or is otherwise unavailable.
- Interactive first-run setup to configure Ollama fallback without touching `.env` manually.
- Copies refined output to clipboard automatically.
- Optionally pastes into Claude/ChatGPT browser windows using **xdotool** (Linux-first).
- Optional Selenium fallback for browser automation.
- Appends all refined prompts to a single **daily log** (`AI Prompts/YYYY-MM-DD.md`) in your Obsidian vault — no more per-query file sprawl.
- Optionally injects recent prompts from today's log as context for better continuity (`--no-context` to disable).
- Maintains a timeline note that keeps growing as new prompts are generated.
- Generates weekly recap notes summarising the daily logs from the last 7 days.

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

Install Ollama for local fallback (optional):

```bash
# Visit https://ollama.ai or:
curl -fsSL https://ollama.ai/install.sh | sh
ollama pull gemma3:1b
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

### Optional environment variables

| Variable | Default | Description |
|---|---|---|
| `GEMINI_MODEL` | *(auto-selected)* | Override the Gemini model used for refinement |
| `OLLAMA_ENABLED` | *(ask on first run)* | `true` / `false` — enable Ollama local fallback |
| `OLLAMA_MODEL` | `gemma3:1b` | Ollama model to use for fallback |
| `OLLAMA_URL` | `http://localhost:11434` | Ollama API endpoint |

On first run of the `refine` command in an interactive terminal, Hades will ask whether you want to enable Ollama fallback and persist your choice to `.env` automatically.

## Usage

### 1) Refine one prompt

```bash
python prompt_refiner.py refine --input "clean this rough idea into a sharp prompt"
```

### 2) Refine continuously (keeps auto-adding knowledge over time)

```bash
python prompt_refiner.py refine --continuous
```

### 3) Refine without recent-context injection

```bash
python prompt_refiner.py refine --input "..." --no-context
```

### 4) Auto-paste with xdotool

```bash
python prompt_refiner.py refine --input "..." --auto-paste --window-name "Claude|ChatGPT"
```

### 5) Selenium fallback (optional)

```bash
python prompt_refiner.py refine --input "..." --auto-paste --selenium-url "https://chatgpt.com"
```

### 6) Weekly recap generation

```bash
python prompt_refiner.py weekly-recap
```

## Obsidian storage

Each refinement is appended to a daily log at `AI Prompts/YYYY-MM-DD.md` inside your vault, using this format:

```markdown
### 10:00:00 UTC

**Original:**
your messy input here

**Refined:**
the clean structured output

---
```

A `_timeline.md` file in `AI Prompts/` links to each daily log as entries are added.

## Weekly automation

Run weekly recap automatically with cron:

```bash
crontab -e
# Every Sunday at 01:00
0 1 * * 0 cd /path/to/hades && /usr/bin/python3 prompt_refiner.py weekly-recap
```

This gives you:

- Continuous Obsidian growth as prompts are refined.
- Automatic weekly recap updates summarising the daily logs.
