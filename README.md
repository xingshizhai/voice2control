# AI Voice Controller

AI Voice Controller is a push-to-talk speech-to-text desktop tool for Windows.  
Hold a hotkey to record, release to transcribe, then automatically paste or send text to the current focused app (for example, Cursor chat).

English | [简体中文](README.zh-CN.md)

## Overview

This project helps developers and power users replace repetitive typing with voice input while keeping control over:

- **When recording starts/stops** (global hotkey)
- **Which ASR backend is used** (`local` WebSocket or `dashscope`)
- **How text is delivered** (`paste_and_send`, `paste_only`, `review`)
- **How domain terms are corrected** (local SQLite lexicon)

## Features

- Global push-to-talk recording with cancel/quit controls
- GUI and CLI modes
- Multi-provider ASR configuration
- Safe text delivery with optional window whitelist
- Clipboard restore and auto-send toggle
- Local terminology lexicon correction
- Lexicon management in GUI and CLI
- CSV import/export for lexicon backup and migration

## Typical Use Cases

- Voice input in Cursor, IDE chat, notes, and internal tools
- Faster writing for repeated domain terms (product names, APIs, acronyms)
- Hybrid setup: local ASR for privacy + cloud ASR fallback when needed

## Quick Start (3 Minutes)

### 1) Install dependencies

```powershell
python -m pip install -r requirements.txt
```

### 2) Create config

```powershell
copy config.example.yaml config.yaml
```

### 3) (Optional) Set DashScope API key via environment variable

```powershell
$env:DASHSCOPE_API_KEY="sk-xxxx"
```

### 4) Launch GUI (default)

```powershell
python -m vc
```

### 5) Validate config in CLI mode

```powershell
python -m vc --cli -c config.yaml --validate-only
```

## How It Works

1. Hotkey pressed -> starts recording
2. Hotkey released -> sends audio to configured ASR
3. Transcript returned -> optional lexicon correction
4. Corrected text -> delivered to focused app
5. History and status updated in GUI/CLI

## Configuration Highlights

Start from `config.example.yaml`. Most users only need:

- `asr.active_provider`
- `asr.providers.*`
- `delivery.mode`
- `delivery.auto_send_enter`
- `gui.minimize_to_tray_on_close`
- `gui.auto_start_listening`
- `lexicon.enabled`
- `lexicon.db_path`
- `lexicon.domain`

## GUI Capabilities

- Start/stop listening
- Toggle recognition on/off at runtime
- Configure provider/mode/whitelist
- Lexicon add/update/delete
- Lexicon search and sorting
- CSV import/export and template generation

## CLI Reference

### Runtime

```powershell
python -m vc
python -m vc --cli -c config.yaml -v
python -m vc --cli -c config.yaml --validate-only
```

### Lexicon Management

```powershell
# Add/update term
python -m vc --lexicon-add "LangChain" --lexicon-aliases "lang chain,郎圈"

# List terms in current domain
python -m vc --lexicon-list

# Import from CSV (term,aliases,domain,weight)
python -m vc --lexicon-import-csv ".\terms.csv"

# Export current domain to CSV
python -m vc --lexicon-export-csv ".\lexicon_export.csv"
```

## Lexicon CSV Format

CSV headers:

```text
term,aliases,domain,weight
```

Example row:

```text
LangChain,"lang chain,郎圈",default,100
```

Import command reports:

- `total`: total rows read
- `imported`: successfully imported rows
- `skipped`: invalid rows (for example empty term or non-integer weight)
- `failed`: unexpected row-level failures

## Default Hotkeys

- `F8`: push-to-talk
- `Esc`: cancel current recording
- `Ctrl+Shift+R`: rerecord (reserved)
- `Ctrl+Q`: quit

## Platform & Requirements

- Python 3.8+
- Windows 10+ (primary validated platform)

## Documentation

- `config.example.yaml`
- `docs/asr-service-api.md`
- `docs/technical-design.md`
- `docs/requirement.md`

## Project Structure

```text
vc/
  app_module/        # app entry orchestration
  __main__.py        # package entry
  ui_module/         # GUI domain module
  core_module/       # core runtime (pipeline/history)
  input_module/      # hotkey/audio capture
  output_module/     # delivery logic
  platform_module/   # platform-specific utilities
  asr_module/        # ASR domain module
  lexicon_module/    # lexicon domain module
  config.py          # typed config loading and validation
tests/
  test_*.py          # unit tests
```

## Development

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

## Build & Packaging (Windows)

Install build dependencies first:

```powershell
python -m pip install -r requirements-dev.txt
```

PowerShell build script:

```powershell
# Build GUI + CLI (onedir)
powershell -ExecutionPolicy Bypass -File .\scripts\build.ps1

# Build GUI only
powershell -ExecutionPolicy Bypass -File .\scripts\build.ps1 -Target gui

# Build CLI only (single executable)
powershell -ExecutionPolicy Bypass -File .\scripts\build.ps1 -Target cli -OneFile
```

Batch build script:

```powershell
scripts\build.bat all
scripts\build.bat gui --onefile
```

Output directory:

- `dist/voice2control` (GUI package)
- `dist/voice2control-cli` (CLI package)

## Known Limitations

- Primary validation target is Windows
- `Ctrl+Shift+R` is reserved and may be expanded in future versions
- GUI automation behavior depends on target app focus and keyboard handling

## FAQ

- **ASR connection refused (10061)?**  
  Check ASR service status, host, port, and firewall rules.

- **DashScope key not found?**  
  Ensure `dashscope_api_key_env` points to an environment variable name such as `DASHSCOPE_API_KEY`.

- **Only paste, no send?**  
  Verify `delivery.mode` and `delivery.auto_send_enter`.

## SEO Keywords

speech to text, push to talk, voice input, desktop dictation, ASR, hotkey recorder, Cursor voice input, local lexicon, SQLite terminology dictionary, Windows voice typing

## Contributing

Issues and pull requests are welcome.  
Please include reproduction steps or test evidence when reporting bugs.

## License

MIT License. See `LICENSE`.

