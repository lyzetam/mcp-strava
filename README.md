# mcp-strava

[![CI](https://github.com/lyzetam/mcp-strava/actions/workflows/ci.yml/badge.svg)](https://github.com/lyzetam/mcp-strava/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Python 3.12 | 3.13 | 3.14](https://img.shields.io/badge/python-3.12%20%7C%203.13%20%7C%203.14-blue)

A read-only [Model Context Protocol](https://modelcontextprotocol.io) server that lets an AI assistant
such as Claude read your own Strava data: activities, laps, splits, zones, second-by-second streams,
segments, routes, gear and totals. Compatible with Strava.

It runs on your machine, talks to Strava with your own API application, and cannot change anything in
your Strava account.

```text
You:     How did Sunday's long run compare with the one the week before?
Claude:  Sunday's 14.2 km took 1:16:40, a 5:24/km average, 11 seconds per km faster than the
         13.8 km the previous Sunday. Your pace held steady until 11 km, then slipped by about
         15 seconds per km on the last climb.
```

*(An illustrative exchange; the numbers are made up.)*

## Contents

- [Features](#features)
- [Tools](#tools)
- [Requirements](#requirements)
- [Quick start](#quick-start)
- [Configuration](#configuration)
- [Privacy and security](#privacy-and-security)
- [Rate limits](#rate-limits)
- [Troubleshooting](#troubleshooting)
- [Development](#development)
- [License and trademarks](#license-and-trademarks)

## Features

- **Read-only by design.** It asks Strava only for read scopes, and apart from the OAuth token exchange
  every request it makes is a `GET`, so no prompt can edit, delete or post anything.
- **14 tools** covering the athlete profile, totals, activities and their detail, laps, zones, streams,
  segments, routes, gear and clubs.
- **Sized for a model's context window.** Activity lists come back as compact summaries with distance
  in metres, kilometres and miles; map polylines are removed; per-second streams are evenly downsampled
  (500 points by default) so every stream still lines up point for point.
- **Signs in once.** `mcp-strava auth` runs the browser OAuth flow and stores the tokens. After that the
  server refreshes its own access token and saves the rotated refresh token Strava hands back.
- **Errors the model can act on.** Strava's own error message, missing-permission hints and rate-limit
  usage reach the assistant instead of a bare "tool failed".
- **No secrets in client config.** Claude Code and Claude Desktop only need the command to run.

## Tools

| Tool | Returns |
| --- | --- |
| `get_athlete` | Your profile: name, location, weight, FTP, units, bikes, shoes and clubs |
| `get_athlete_stats` | Recent (4 weeks), year-to-date and all-time ride, run and swim totals |
| `get_athlete_zones` | Your heart-rate and power zones |
| `list_activities` | Compact activity summaries, filtered by `after` / `before` date and paged |
| `get_activity` | Full detail for one activity: splits, laps, calories, device, power; optional best efforts |
| `get_activity_laps` | The laps of one activity |
| `get_activity_zones` | Time spent in each heart-rate, power or pace zone during one activity |
| `get_activity_streams` | Per-second time, distance, position, altitude, speed, heart rate, cadence, power and more |
| `list_starred_segments` | Segments you have starred |
| `get_segment` | One segment, including your personal record on it |
| `list_segment_efforts` | Your efforts on one segment, optionally within a date range |
| `list_routes` | Routes you created |
| `get_gear` | One bike or pair of shoes, with total distance |
| `list_clubs` | Clubs you belong to |

Every tool is annotated `readOnlyHint: true`. Units are Strava's: metres, seconds and metres per second.

## Requirements

- macOS or Linux. Windows is untested.
- [uv](https://docs.astral.sh/uv/getting-started/installation/), which also provides Python 3.12 or
  newer if you do not have it.
- A Strava account and an MCP client such as [Claude Code](https://docs.claude.com/en/docs/claude-code)
  or [Claude Desktop](https://claude.ai/download).

## Quick start

### 1. Create your own Strava API application

Open <https://www.strava.com/settings/api> and create an application:

| Field | Value |
| --- | --- |
| Application Name | Anything that does **not** contain the word "Strava" (Strava's brand guidelines forbid it) |
| Category | Any, e.g. *Performance Analysis* |
| Website | Any URL you own |
| Authorization Callback Domain | `localhost` |

Keep the page open: the next step asks for its **Client ID** and **Client Secret**.

### 2. Install

```bash
uv tool install git+https://github.com/lyzetam/mcp-strava
mcp-strava --version
```

This puts an `mcp-strava` command on your `PATH`. Upgrade later with `uv tool upgrade mcp-strava`.

### 3. Authorize once

```bash
mcp-strava auth
```

It asks for the Client ID and Client Secret (the secret is not echoed), opens Strava's consent page in
your browser, and waits for the redirect on `http://localhost:8723/callback`. Leave every permission box
ticked; the command warns you if one was unticked. The tokens are saved to
`~/.config/mcp-strava/tokens.json`.

| Option | Default | Purpose |
| --- | --- | --- |
| `--port PORT` | `8723` | Local port for the OAuth redirect |
| `--timeout SECONDS` | `300` | How long to wait for you to approve |
| `--no-browser` | off | Print the authorize URL instead of opening a browser |

You only run this again if you revoke access in Strava or the server tells you to.

### 4. Connect your MCP client

The server speaks MCP over stdin and stdout and takes no arguments. Desktop apps often start with a
minimal `PATH`, so give them the absolute path that `command -v mcp-strava` prints.

**Claude Code**

```bash
claude mcp add --scope user strava -- "$(command -v mcp-strava)"
claude mcp list    # strava: ... - ✔ Connected
```

**Claude Desktop**: add the server to `claude_desktop_config.json` (on macOS in
`~/Library/Application Support/Claude/`), then restart Claude Desktop:

```json
{
  "mcpServers": {
    "strava": {
      "command": "/Users/you/.local/bin/mcp-strava"
    }
  }
}
```

**Any other MCP client**: register a stdio server whose command is the same absolute path.

Then ask something like *"How far did I run this month?"*, *"Compare my pace across my last five
runs"* or *"Where did my heart rate drift on Sunday's long ride?"*

## Configuration

Nothing is required after `mcp-strava auth`. These environment variables exist for unusual setups:

| Variable | Purpose |
| --- | --- |
| `STRAVA_TOKEN_FILE` | Where the token file lives. Default `~/.config/mcp-strava/tokens.json` |
| `STRAVA_CLIENT_ID`, `STRAVA_CLIENT_SECRET` | App credentials, used by `mcp-strava auth` instead of prompting, and by the server when the token file lacks them |
| `STRAVA_REFRESH_TOKEN` | A refresh token to start from when the token file has none |

The server re-reads the token file on every request, so re-running `mcp-strava auth` takes effect
without restarting your MCP client.

## Privacy and security

- Your data travels between Strava, this process and your MCP client, nowhere else. What the client
  then sends to its model is governed by that client's data policy.
- The token file holds your app's client secret and your tokens. It is written atomically with mode
  `0600`, in a directory created as `0700`. Treat it like a password.
- To cut access off completely, revoke the application at <https://www.strava.com/settings/apps> and
  delete the token file.
- Strava's [API Agreement](https://www.strava.com/legal/api) only lets a user's data be shown to that
  user. This server is built for exactly that: one person, their own API application, their own data.
  Do not host it for other people.

See [SECURITY.md](SECURITY.md) for the security model and how to report a vulnerability, and
[docs/architecture.md](docs/architecture.md) for how the pieces fit together.

## Rate limits

A new Strava API application gets 200 requests per 15 minutes and 2,000 per day overall, of which 100
per 15 minutes and 1,000 per day may be reads. The 15-minute window resets at :00, :15, :30 and :45; the
daily one at midnight UTC. When a limit is hit the assistant receives the current usage from Strava's
`X-RateLimit-*` headers, so it can tell you when to try again. Typical questions use one to five
requests; filtering `list_activities` by date keeps it that way.

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| *"Strava is not authorized"* or *"Strava rejected the refresh token"* | Access was revoked or never granted. Run `mcp-strava auth`. |
| An error naming `activity:read_permission` or another `_permission` | A permission box was unticked during consent. Run `mcp-strava auth` and leave every box ticked. |
| Heart rate is missing from activities | Strava only shares health data the athlete allowed under **Settings → Data Permissions** on strava.com. |
| *"Could not reach Strava"* | A network, DNS or proxy problem between your machine and `www.strava.com`. |
| `mcp-strava auth` reports the port is in use | Pick another one: `mcp-strava auth --port 8724`. |
| The browser shows *"This link is from an earlier login attempt"* | You reloaded an old redirect. Approve again from the URL the current `mcp-strava auth` printed. |
| Claude Desktop does not list the server | Use the absolute command path, restart Claude Desktop, and check `~/Library/Logs/Claude/mcp-server-strava.log`. |
| Private activities are missing | They need the `activity:read_all` permission; run `mcp-strava auth` and keep that box ticked. |

## Development

```bash
git clone https://github.com/lyzetam/mcp-strava.git && cd mcp-strava
uv sync
uv run pytest --cov=mcp_strava     # tests and coverage
uv run ruff check . && uv run ruff format --check . && uv run mypy
```

Tests never touch the network: the Strava API is faked at the HTTP boundary and every tool is called
through a real MCP client session. [CONTRIBUTING.md](CONTRIBUTING.md) explains the workflow and how to
add a tool, and [CHANGELOG.md](CHANGELOG.md) lists what changed in each release.

## License and trademarks

Released under the [MIT License](LICENSE).

Strava is a trademark of Strava, Inc. This project is independent and is not affiliated with, endorsed
by or sponsored by Strava. Use of the Strava API is subject to the
[Strava API Agreement](https://www.strava.com/legal/api) and
[brand guidelines](https://developers.strava.com/guidelines/).
