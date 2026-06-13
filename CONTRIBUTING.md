# Contributing to PDClaw

Thanks for your interest in contributing!

## How It Works

PDClaw is a tag-driven automation system that orchestrates software development via GitHub Issues. See [README.md](./README.md) for the full overview.

## Development Setup

```bash
# Clone the repo
git clone https://github.com/billqbwen/pdclaw.git
cd pdclaw

# Install Python dependencies
pip install requests

# Set required environment variables
export GITHUB_TOKEN=your_token_here
export DEEPSEEK_API_KEY=your_key_here

# Run in development mode (single issue, one cycle)
python pdclaw.py --issue https://github.com/owner/repo/issues/42 --once --auto-run --verbose
```

## Project Structure

| Path | Description |
|------|-------------|
| `pdclaw.py` | Core PDCA automation engine |
| `pdca_memory.py` | Persistent memory system |
| `pdca_claude_session.py` | Stateful AI session manager |
| `pdca_memory_cli.py` | CLI for memory management |
| `skills/` | PDCA skill definitions |

## Submitting Changes

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Submit a pull request

## Code Style

- Python: Follow PEP 8
- Keep changes focused and minimal
- Update documentation when changing behavior
