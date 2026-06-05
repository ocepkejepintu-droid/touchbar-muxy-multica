# TouchBar Muxy Multica

BetterTouchTool Touch Bar widget that displays real-time Muxy/Multica agent notification status from Agentmax/OMX on the MacBook Pro Touch Bar.

## Overview

A compact, read-only status collector that runs as a BetterTouchTool Shell Script / Task Widget. It tails Agentmax/OMX runtime state and renders a concise status line on the Touch Bar — no Python tracebacks, no messy output.

## Structure

```
├── scripts/
│   ├── agentmax_status.py        # Core status collector (stdlib-only, read-only by default)
│   ├── btt_agentmax_widget.sh    # BTT shell-script widget wrapper
│   ├── btt_muxy_tap.sh           # Tap action helper for Muxy app
│   └── btt_multica_tap.sh        # Tap action helper for Multica app
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
2. Set name to `Muxy Notification Center`
3. Set refresh interval to `2` seconds
4. Set width to `240` px (210–280 px works well)
5. Point script path to `scripts/btt_agentmax_widget.sh`

Tap actions are assigned manually in BTT — see the tap helpers in `scripts/`.

## Requirements

- macOS with Touch Bar
- [BetterTouchTool](https://folivora.ai/)
- [Agentmax/OMX](https://github.com/Agentmax-OMX) runtime (`.omx` state directory)
- Python 3 (stdlib only, no pip dependencies)

## License

MIT
