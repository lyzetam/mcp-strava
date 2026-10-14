## What and why

<!-- What does this change, and what problem does it solve? Link the issue if there is one. -->

## How I tested it

<!-- Commands you ran and what you saw. For a new tool, a sample response with personal data removed. -->

## Checklist

- [ ] `uv run ruff check . && uv run ruff format --check .` pass
- [ ] `uv run mypy` passes
- [ ] `uv run pytest` passes, with a test that covers this change
- [ ] Still read-only: no new scopes and no non-`GET` requests to the Strava API
- [ ] `README.md` and `CHANGELOG.md` updated if behaviour changed
