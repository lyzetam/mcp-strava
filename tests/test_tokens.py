import stat

import pytest

from mcp_strava.tokens import DEFAULT_TOKEN_FILE, TokenFileError, TokenStore, token_path


def test_load_missing_file_is_empty(store: TokenStore):
    assert store.load() == {}


def test_save_round_trips_with_owner_only_permissions(store: TokenStore):
    store.save({"refresh_token": "r", "expires_at": 1})

    assert store.load() == {"refresh_token": "r", "expires_at": 1}
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    assert stat.S_IMODE(store.path.parent.stat().st_mode) == 0o700


def test_save_replaces_atomically_and_leaves_no_temp_files(store: TokenStore):
    store.save({"refresh_token": "first"})
    store.save({"refresh_token": "second"})

    assert store.load() == {"refresh_token": "second"}
    assert [p.name for p in store.path.parent.iterdir()] == ["tokens.json"]


def test_token_path_defaults_to_config_dir():
    assert token_path({}) == DEFAULT_TOKEN_FILE.expanduser()


def test_token_path_honours_env_override(tmp_path):
    assert token_path({"STRAVA_TOKEN_FILE": str(tmp_path / "t.json")}) == tmp_path / "t.json"


@pytest.mark.parametrize("content", ["{not json", "[]", "\udcff"])
def test_load_rejects_a_file_that_is_not_a_json_object(store: TokenStore, content: str):
    store.path.parent.mkdir(parents=True)
    store.path.write_bytes(content.encode("utf-8", "surrogateescape"))

    with pytest.raises(TokenFileError, match=str(store.path)):
        store.load()
