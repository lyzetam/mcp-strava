# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-10-14

First public release.

### Added

- Read-only MCP server over stdio with 14 tools: `get_athlete`, `get_athlete_stats`,
  `get_athlete_zones`, `list_activities`, `get_activity`, `get_activity_laps`, `get_activity_zones`,
  `get_activity_streams`, `list_starred_segments`, `get_segment`, `list_segment_efforts`,
  `list_routes`, `get_gear` and `list_clubs`.
- `mcp-strava auth`: one-time browser OAuth with a `127.0.0.1` callback listener, `state` checking,
  a hidden prompt for the client secret, and a warning when a permission box was unticked.
- Automatic access-token refresh with the rotated refresh token written back atomically to a `0600`
  token file.
- Compact activity summaries, polyline stripping and even stream downsampling to keep responses
  small.
- Errors that reach the model with Strava's message: missing permissions, revoked access, rate-limit
  usage and network failures.
- `--help` and `--version` for the command line.

[Unreleased]: https://github.com/lyzetam/mcp-strava/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/lyzetam/mcp-strava/releases/tag/v0.1.0
