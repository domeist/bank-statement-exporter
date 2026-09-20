import json

import pytest

from bank_statement_exporter.config import DEFAULT_REDIRECT_URI, ConfigError, load_config

FILLED = {"monzo_client_id": "oauth2client_x", "monzo_client_secret": "mnzconf_x"}


def write(tmp_path, data):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data))
    return path


def test_with_no_config_the_app_still_has_usable_defaults(tmp_path):
    cfg = load_config(tmp_path / "config.json")
    assert not cfg.monzo_enabled
    assert cfg.redirect_uri == DEFAULT_REDIRECT_URI
    assert cfg.monzo_token_path.endswith("/config/monzo-token.json")
    assert "Groceries" in cfg.categories


def test_filled_credentials_enable_monzo(tmp_path):
    assert load_config(write(tmp_path, FILLED)).monzo_enabled


def test_half_filled_credentials_do_not_enable_monzo(tmp_path):
    assert not load_config(write(tmp_path, {"monzo_client_id": "only-the-id"})).monzo_enabled


def test_untouched_example_placeholders_count_as_not_configured(tmp_path):
    cfg = load_config(write(tmp_path, {
        "monzo_client_id": "YOUR_MONZO_CLIENT_ID",
        "monzo_client_secret": "YOUR_MONZO_CLIENT_SECRET",
        "monzo_token_path": "/path/to/token.json",
    }))
    assert not cfg.monzo_enabled
    assert cfg.monzo_token_path.endswith("/config/monzo-token.json")


def test_invalid_json_is_reported_clearly(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{not json")
    with pytest.raises(ConfigError, match="not valid JSON"):
        load_config(path)


def test_a_json_list_is_rejected(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("[]")
    with pytest.raises(ConfigError, match="JSON object"):
        load_config(path)


def test_relative_paths_resolve_against_the_repo_root(tmp_path):
    cfg = load_config(write(tmp_path, {**FILLED, "monzo_token_path": "config/mine.json"}))
    assert cfg.monzo_token_path.startswith("/")
    assert cfg.monzo_token_path.endswith("/config/mine.json")


def test_categories_and_category_map_can_be_overridden(tmp_path):
    cfg = load_config(write(tmp_path, {
        **FILLED, "categories": ["", "Fun"], "monzo_category_map": {"eating_out": "Fun"},
    }))
    assert cfg.categories == ["", "Fun"]
    assert cfg.monzo_category_map == {"eating_out": "Fun"}


def test_a_nonsense_timezone_is_rejected_with_a_helpful_message(tmp_path):
    with pytest.raises(ConfigError, match="not a known timezone"):
        load_config(write(tmp_path, {**FILLED, "timezone": "Not/AZone"}))


def test_a_real_timezone_is_accepted(tmp_path):
    assert load_config(write(tmp_path, {**FILLED, "timezone": "UTC"})).timezone == "UTC"


def test_categories_given_as_a_string_are_rejected_not_split_into_letters(tmp_path):
    with pytest.raises(ConfigError, match="must be a list"):
        load_config(write(tmp_path, {**FILLED, "categories": "Groceries"}))


def test_categories_given_as_a_number_are_rejected(tmp_path):
    with pytest.raises(ConfigError, match="must be a list"):
        load_config(write(tmp_path, {**FILLED, "categories": 5}))


def test_a_category_map_that_is_not_a_mapping_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match="must be an object"):
        load_config(write(tmp_path, {**FILLED, "monzo_category_map": ["a", "b"]}))
