# Security policy

## Supported versions

Only the latest release receives security fixes.

## Reporting a vulnerability

Please do not open a public issue for a security problem. Report it privately through
[GitHub's private vulnerability reporting](https://github.com/lyzetam/mcp-strava/security/advisories/new).
You can expect an acknowledgement within 7 days. Once a fix is released, the advisory is published
and the reporter is credited unless they ask not to be.

Useful details: the version or commit, your OS and Python version, what an attacker can do, and the
smallest steps that reproduce it. Never include real Strava tokens or client secrets in a report.

## Security model

`mcp-strava` runs on your own machine and talks to two parties only: the MCP client that launched it
(over stdio) and `www.strava.com` (over HTTPS).

- **Read-only by construction.** It requests only the `read`, `read_all`, `profile:read_all` and
  `activity:read_all` scopes. Apart from the OAuth token exchange (`POST /oauth/token`), every request
  it makes is a `GET`. A compromised or confused model cannot change your Strava account through it.
- **Secrets stay in one file.** The client secret, access token and refresh token live in
  `~/.config/mcp-strava/tokens.json` (override with `STRAVA_TOKEN_FILE`). The file is written
  atomically with mode `0600`, in a directory created as `0700`, so it is never half-written or briefly
  readable by other users. MCP client configs need no secrets at all.
- **Secrets stay out of output.** Tokens and the client secret never appear in tool results, error
  messages or `mcp-strava auth` output.
- **The OAuth callback listener is local and short-lived.** `mcp-strava auth` binds `127.0.0.1` only,
  accepts a redirect only when its `state` matches the attempt it started, and exits after one
  successful callback or the timeout.
- **Model-supplied arguments are constrained.** Numeric IDs are typed as integers by the tool schema,
  and gear IDs are validated before they are placed in a URL path.

### What it does not protect against

Anyone who can read your user account's files can read the token file. Anything the server returns is
passed to the model your MCP client uses, under that client's data policy. If you suspect the token
file was exposed, revoke access at <https://www.strava.com/settings/apps> and run `mcp-strava auth`
again.
