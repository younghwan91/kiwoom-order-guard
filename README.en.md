<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/images/brand/wordmark-dark.svg">
  <img alt="kiwoom-order-guard" src="docs/images/brand/wordmark-light.svg" height="56">
</picture>

[한국어](README.md) | **English**

**A Claude Code hook that inspects the *contents* of orders submitted by AI agents via Kiwoom's official MCP and plugin before transmission.**

[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/github/license/younghwan91/kiwoom-order-guard)](LICENSE)
[![LinkedIn](https://img.shields.io/badge/LinkedIn-younghwan--chae-0A66C2?logo=linkedin&logoColor=white)](https://www.linkedin.com/in/younghwan-chae/)

Official tools check *whether a human confirmed*. This hook checks *whether the order is worth confirming*.

```text
kiwoom-order-guard blocked:
- Order amount 7,000,000 KRW (100 shares × 70,000 KRW) exceeds cap 1,000,000 KRW
Limits are changed directly by the user (`kog config` in terminal).
```

- **Direct order content inspection:** Enforces per-order amount and quantity caps, market-buy blocks, 120-second duplicate-buy cooldowns, and daily/per-ticker attempt limits before submission.
- **Anti-bypass structural parsing:** Parses command structure (`orders … --confirm`, `order-preview`, MCP `kiwoom_order_submit`) rather than executable names, and blocks unparseable orders using variables, loops, or script writes.
- **Zero dependencies · 45ms startup:** Uses only the Python 3.11+ standard library and runs as a `uv`-based hook in ~90ms per Bash/MCP call (pre + post hooks).
- **Human-only control:** Setting limits (`kog init` / `kog config`) and clearing emergency stop (`kog resume`) can only be run by the user in a terminal; agent attempts to modify configuration are blocked.

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

- Config path: Linux/macOS `~/.config/kiwoom-order-guard/config.toml`, Windows `%APPDATA%\kiwoom-order-guard\config.toml`.
- Unknown keys or malformed values block orders so that typoed limits are never silently ignored.
- Once `kog init` has been run, orders are still blocked if the config file is deleted (the initialization marker lives in a separate state directory).

## Why It Exists

In Aug–Sep 2026, Kiwoom released an official MCP server (`Kiwoom-REST-API/mcp_exec`) and Claude Code plugin (`Kiwoom-Plugin`).
Both guard the order **path**: order tools are disabled by default, follow preview → human confirmation → submit, and require explicit approval for live accounts.
Neither inspects the order **contents**. As noted in the official server code:

> confirm can be set by the caller (an AI included). Human approval relies on the client's
> tool-execution approval or a future plugin hook.
> — `mcp_exec/src/kiwoom_exec_mcp/server.py`

When an agent proposes orders and a human repeatedly clicks "Approve", confirmation becomes a habit.
This hook catches the orders that habit misses.

```mermaid
flowchart LR
    Agent["AI Agent\n(Claude Code)"] -->|"Bash / MCP call"| Pre["PreToolUse Hook\nParse command · Check limits/rules"]
    Pre -->|"Violation / Unparseable / Config tampering"| Block["Block (return reason)"]
    Pre -->|"Pass"| Exec["kiwoomcli / order-preview\n/ kiwoom_order_submit"]
    Exec -->|"Execution complete"| Post["PostToolUse Hook\nRecord daily/ticker counts · Duplicate cooldown"]
```

## Inspection Rules & Defaults

| Check | Default | Scope |
|---|---|---|
| Per-order amount cap | Domestic 1,000,000 KRW / Overseas $1,000 | New buys, domestic modifies |
| Per-order quantity cap | 1,000 shares | New buys, domestic modifies |
| Market-type buys (unknown upfront amount) | Blocked | New buys |
| Repeated identical buy (agent retry loop) | Blocked for 120s | New buys |
| Daily / per-ticker buy attempts | 20 / 5 | New buys |
| Allowlist (when non-empty) | Disabled | New buys |
| Blocklist | Empty | Both buys and sells (prevents agent from selling held positions) |
| Emergency stop (`kog stop`) | Off | Buys, modifies |
| Overseas modifies, FX exchange (guard cannot measure amount) | Blocked | — |
| Agent attempts to modify guard config or disable the guard | Blocked | All tools |

- **Sells** are exempt from amount/quantity caps, count limits, and duplicate checks so liquidation during sharp drops is never blocked. Set `apply_caps_to_sell` to cap sells as well.
  **Cancels** reduce risk and pass through.
- **Orders are matched by command structure, not executable name.** After unquoting, it looks for `orders <write-cmd> … --confirm` and `order-preview`, so `$KW`, `ki''woomcli`, and `python -m kiwoom_cli` all undergo the same inspection.
- **Unparseable orders are blocked**, including:
  - Orders using stock names or variables (`$C`), or abbreviated flags (`--mark`)
  - Orders inside loops, functions, `xargs`, `watch`, or `find -exec`
  - Orders wrapped in `bash -c "…"`, `$( )`, or quotes
  The agent receives a reason instructing it to run with explicit ticker codes, one order at a time.
  Writing script files containing orders (`.sh`, `.py`, `.ps1`, etc.) is also blocked.
- **Counts only track executed calls.** Recording happens in `PostToolUse`, so orders rejected by the human at the permission prompt are not counted.
  If a single call contains multiple orders, each earlier order is counted before evaluating the next.

## Inspected Order Paths

Input shapes are taken directly from official specifications.

| Path | When Inspected | Source |
|---|---|---|
| Plugin `python -m kw_runtime.cli order-preview …` | When creating the order preview (submission only accepts preview ID) | `Kiwoom-Plugin` `skills/kiwoom-order/SKILL.md`, `kw_runtime/cli.py` |
| `kiwoomcli domestic\|overseas orders <buy\|sell\|modify\|cancel\|credit-*\|gold-*> … --confirm`, `overseas exchange request --confirm` | When `--confirm` is present | kwcli 1.0.3 `maps/arguments.csv`, `api_commands.csv` (17 `order_write` types) |
| MCP `kiwoom_order_submit(command_path, options, confirm)` | When `confirm` is not falsy | `mcp_exec/.../server.py` |

When the official plugin is installed, direct `kiwoomcli … --confirm` calls are already blocked by the official `prod_guard`.
Inspection on that path matters when using only the MCP server or kwcli without the plugin.

## Limitations

This hook is a seatbelt, not a vault.

- **Calls bypassing hooks.** The following are not seen:
  - Running an already-existing script file (`bash o.sh`) — only writing new scripts is blocked
  - Agent calling REST APIs directly from Python
  - Direct `POST` requests to the plugin runtime HTTP endpoint
  - Manual orders clicked by a human in the Trading Surface UI
- **Outside Claude Code.** Hooks only run in Claude Code; MCP calls from Claude Desktop, Codex, or Cursor pass through.
  The official plugin docs note the same limitation.
- **When the hook fails to start.** Without `uv` or Python 3.13, Claude Code passes failed hooks through rather than blocking.
  The official plugin behaves the same way.
- **Cannot guarantee prevention of guard tampering.** Access to config/state directories, `kog resume`, disabling the plugin, and `disableAllHooks` are blocked,
  but string matching can be worked around by a determined agent. Account-level limits remain the true last line of defense.
- **No market-price checks.** Previous close, price-limit bands, and deviation from current price are not in v0.1;
  they will not be added until quote response fields are verified against the live server.
- **No risky-disclosure filtering.** Blocking halted, managed, or same-day rights-offering/CB-disclosure stocks is planned for the next release.
- **When config cannot be read.** If the config file has a typo or is missing, buys and modifies are blocked, while cancels and sells pass through.
  The blocklist is not applied in this state.
- **Fail-safe false positives.** Order examples written inside heredoc documents are parsed and inspected as orders.
  Text-only commands such as `grep`, `echo`, `cat`, and `git` are exempted.
- **Untested on real Windows hardware.** Only UTF-8 stdin reading has been verified in tests (simulating a cp949 locale).

## CLI Commands (`kog`)

| Command | Description |
|---|---|
| `kog init` / `kog config` | Create default config / Show config path and contents |
| `kog status` | Today's buy attempt count and emergency stop state |
| `kog stop` / `kog resume` | Enable / clear emergency stop (`resume` cannot be run by an agent) |
| `kog check '<cmd>'` | Dry-run inspection of a single Bash command (no recording) |

## Development

```bash
uv sync
uv run pytest -q
```

## Related & Documentation

- [docs/ledger.md](docs/ledger.md) — State ledger and recording schema
- [kiwoom-client](https://github.com/younghwan91/kiwoom-client) — Kiwoom REST Python library by the same author; its built-in MCP server does not register order tools on live accounts by default.

Not investment advice. Order responsibility rests with the account owner. [MIT](LICENSE).

---

**Younghwan Chae (채영환)** · [GitHub @younghwan91](https://github.com/younghwan91)
