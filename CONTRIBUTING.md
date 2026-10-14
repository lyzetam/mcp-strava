# Contributing

Thanks for taking the time to improve `mcp-strava`. Bug reports, fixes and new read-only tools are
all welcome.

## Ground rules

- **Read-only stays read-only.** Pull requests that add write scopes or write endpoints will not be
  merged. The project's safety argument depends on it.
- **One change per pull request**, with a test that fails before the change and passes after it.
- **Never commit real credentials or Strava data.** Tests use fake tokens and made-up activities.

## Development setup

You need [uv](https://docs.astral.sh/uv/) (it installs the right Python for you).

```bash
git clone https://github.com/lyzetam/mcp-strava.git
cd mcp-strava
uv sync
```

## Checks

CI runs exactly these, and a pull request needs all of them green:

```bash
uv run ruff check .                 # lint
uv run ruff format --check .        # formatting (run `uv run ruff format .` to fix)
uv run mypy                         # strict type check
uv run pytest --cov=mcp_strava      # tests; coverage must stay at or above 90%
```

## How the tests work

Tests never touch the network. The Strava API is replaced at the HTTP boundary by
`httpx.MockTransport` (see `FakeStrava` in `tests/conftest.py`), and every tool is called through a
real in-memory MCP client session, so a test exercises the same path a model does. The OAuth callback
tests use a real socket on `127.0.0.1` with an ephemeral port.

To try a change against your own account, run `uv run mcp-strava auth` once, then point an MCP client
or the [MCP Inspector](https://github.com/modelcontextprotocol/inspector) at `uv run mcp-strava`.

## Adding a tool

1. Add the endpoint call in `src/mcp_strava/server.py` with `annotations=READ_ONLY`, typed arguments,
   and a docstring written for the model: what it returns, its units, and when to use it.
2. Keep responses small: strip polylines, page lists, and downsample anything per-second.
3. Add tests in `tests/test_server.py` that call the tool through the MCP session.
4. Add a row to the tools table in `README.md` (a test checks that every tool is listed).
5. Note it under `Unreleased` in `CHANGELOG.md`.

## Commit messages

[Conventional Commits](https://www.conventionalcommits.org/): `feat: …`, `fix: …`, `docs: …`,
`test: …`, `refactor: …`, `chore: …`, with the reason for the change in the body.
