# TouchBar Vibe Island

BetterTouchTool Touch Bar widget that mirrors your live Vibe Island agent sessions on the MacBook Pro Touch Bar.

## Overview

A compact, read-only status collector that runs as a BetterTouchTool Shell Script / Task Widget. It reads Vibe Island's local session state and shows which agents are working, which project they're on, and whether a permission needs attention. OMX/Muxy and Multica remain optional fallbacks.

## Structure

```
├── scripts/
│   ├── agentmax_status.py        # Core status collector (stdlib-only, read-only by default)
│   ├── btt_agentmax_widget.sh    # BTT shell-script widget wrapper
│   ├── btt_vibe_island_tap.sh    # Tap action helper for Vibe Island
│   ├── btt_muxy_tap.sh           # Legacy tap helper for Muxy app
│   └── btt_multica_tap.sh        # Legacy tap helper for Multica app
├── config/
│   ├── project-aliases.json      # Project alias mappings
│   ├── status-protocol.json      # Status protocol definition
│   └── btt-widget.example.json   # Example BTT widget config
├── tests/
│   ├── test_agentmax_status.py   # Test suite
│   └── fixtures/
├── docs/
│   ├── BTT_SETUP.md              # BetterTouchTool setup guide
│   ├── STATUS_PROTOCOL.md        # Status protocol documentation
│   └── DEBUGGING.md              # Debugging guide
├── setup.sh                      # Local setup helper (safe, no destructive actions)
└── prompt-exports/               # Architecture planning artifacts
```

## Quick Start

```sh
# Make scripts executable and print setup instructions
./setup.sh
```

Then in BetterTouchTool:
1. Add a **Shell Script / Task Widget** to the Touch Bar
2. Set name to `Vibe Island Touch Bar`
3. Set refresh interval to `2` seconds
4. Set width to `240` px (210–280 px works well)
5. Point script path to `scripts/btt_agentmax_widget.sh`
6. Assign tap action to `scripts/btt_vibe_island_tap.sh`

## Requirements

- macOS with Touch Bar
- [BetterTouchTool](https://folivora.ai/)
- [Vibe Island](https://vibeisland.app/) running with active agent sessions
- Python 3 (stdlib only, no pip dependencies)

## License

MIT