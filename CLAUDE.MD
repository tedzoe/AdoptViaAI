# AdoptviaAI — Claude Code Instructions

## Project
CLI tool demonstrating Claude API patterns for CCA-F certification.
Command: avai | Language: Python 3.10+ | Package manager: pip

## Code Standards
- Always use Rich library for terminal output
- Full file rewrites only, no partial edits
- Add CCA-F domain comments on every new component
- Default model is always haiku unless testing quality

## Cost Rules
- Never hardcode API keys
- Always include dry-run mode for new features
- Log all API calls to logs/usage.log

## Structure
- Core logic in core/
- CLI entry points in main.py
- New prompts go in prompts/
- New tools go in tools/
- New agents go in agents/

## Testing
- Test with --dry-run first
- Use haiku for all dev/testing
- Only switch to sonnet for final validation

## Never
- Modify .env directly
- Commit logs/ contents
- Use argparse (we use Click)
- Create files outside the defined structure