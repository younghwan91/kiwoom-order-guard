<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/images/brand/wordmark-dark.svg">
  <img alt="kiwoom-order-guard" src="docs/images/brand/wordmark-light.svg" height="52">
</picture>

# kiwoom-order-guard

**A Claude Code hook that inspects the *contents* of orders submitted by AI agents via Kiwoom's official MCP and plugin before transmission**

Official tools check *whether a human confirmed*. This hook checks *whether the order is worth confirming*.

[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/github/license/younghwan91/kiwoom-order-guard)](LICENSE)
[![LinkedIn](https://img.shields.io/badge/LinkedIn-younghwan--chae-0A66C2?logo=linkedin&logoColor=white)](https://www.linkedin.com/in/younghwan-chae/)

[Quick Start](#quick-start) · [Detailed Guide](docs/guide.en.md) · [State Ledger](docs/ledger.md) · [한국어](README.md)

</div>

```text
kiwoom-order-guard blocked:
- Order amount 7,000,000 KRW (100 shares × 70,000 KRW) exceeds cap 1,000,000 KRW
Limits are changed directly by the user (`kog config` in terminal).
```

<p align="center"><sub>Orders exceeding amount, quantity, or duplicate-buy limits are blocked with a reason before execution.</sub></p>

## Quick Start

Install the plugin in Claude Code:

```text
/plugin marketplace add younghwan91/kiwoom-order-guard
/plugin install kiwoom-order-guard@kiwoom-order-guard
```

Initialize and inspect limits **directly as a human** in the terminal:

```bash
uvx --from git+https://github.com/younghwan91/kiwoom-order-guard kog init     # Create default config file
uvx --from git+https://github.com/younghwan91/kiwoom-order-guard kog config   # Show config path and contents
```

- **Config path**: Linux/macOS `~/.config/kiwoom-order-guard/config.toml`, Windows `%APPDATA%\kiwoom-order-guard\config.toml`.
- **Fail-closed on errors**: Unknown keys, malformed values, or a missing config file after `kog init` block buy and modify orders.

## Key Features

- **Direct order content inspection**: Enforces per-order amount and quantity caps, market-buy blocks, 120-second duplicate-buy cooldowns, and daily/per-ticker attempt limits before submission.
- **Anti-bypass structural parsing**: Parses command structure (`orders … --confirm`, `order-preview`, MCP `kiwoom_order_submit`) rather than executable names, and blocks unparseable orders using variables, loops, or script writes.
- **Zero dependencies · 45ms startup**: Uses only the Python 3.11+ standard library and runs as a `uv`-based hook in ~90ms per Bash/MCP call (pre + post hooks).
- **Human-only control**: Setting limits (`kog init`, `kog config`) and clearing emergency stop (`kog resume`) can only be run by the user in a terminal; agent attempts to modify configuration are blocked.

## Inspection Rules & Defaults

| Check | Default | Scope |
|---|---|---|
| Per-order amount cap | Domestic 1,000,000 KRW / Overseas $1,000 | New buys, domestic modifies |
| Per-order quantity cap | 1,000 shares | New buys, domestic modifies |
| Market-type buys (unknown upfront amount) | Blocked | New buys |
| Repeated identical buy (agent retry loop) | Blocked for 120s | New buys |
| Daily / per-ticker buy attempts | 20 / 5 | New buys |
| Allowlist (when non-empty) | Disabled | New buys |
| Blocklist | Empty | Both buys and sells |
| Emergency stop (`kog stop`) | Off | Buys, modifies |
| Overseas modifies, FX exchange (guard cannot measure amount) | Blocked | — |
| Agent attempts to modify guard config or disable the guard | Blocked | All tools |

See [Inspection Rule Details](docs/guide.en.md#inspection-rule-details) for sell/cancel exemptions, structural parsing, and `PostToolUse` counting.

## Limitations

This hook is a seatbelt, not a vault.

- **Calls bypassing hooks**: Existing script runs (`bash o.sh`), direct Python REST calls, direct runtime HTTP `POST` requests, and manual Trading Surface UI orders are not seen.
- **Outside Claude Code & startup failures**: MCP calls from Claude Desktop, Codex, or Cursor pass through, as do failed hooks when `uv` or Python 3.13 is missing.
- **No market-price or disclosure checks**: Previous close, price-limit bands, and halted/managed/rights-offering/CB-disclosure filtering are not yet supported.
- **Tampering & config errors**: String-matching protections can be worked around by a determined agent; on config errors or missing files, buys and modifies are blocked while cancels and sells pass through.

See [Limitations in Detail](docs/guide.en.md#limitations-in-detail) for the full list and false-positive exceptions.

## Documentation

| Doc | Contents |
|---|---|
| [Detailed Guide](docs/guide.en.md) | Background, rule details, inspected order paths, `kog` CLI, and development |
| [State Ledger](docs/ledger.md) | State ledger and recording schema (Korean) |
| [kiwoom-client](https://github.com/younghwan91/kiwoom-client) | Kiwoom REST Python library by the same author |

## License

Not investment advice. Order responsibility rests with the account owner. [MIT](LICENSE) — **Younghwan Chae (채영환)** · [GitHub @younghwan91](https://github.com/younghwan91)
