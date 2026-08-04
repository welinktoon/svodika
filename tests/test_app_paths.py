"""Tests for update-safe per-user application storage."""

from services.app_paths import (
    APP_DATA_ENV,
    get_app_data_dir,
    migrate_legacy_user_data,
)


def test_explicit_data_directory_is_created(monkeypatch, tmp_path):
    destination = tmp_path / "user-data"
    monkeypatch.setenv(APP_DATA_ENV, str(destination))

    assert get_app_data_dir() == destination.resolve()
    assert destination.is_dir()


def test_legacy_migration_copies_without_overwriting(tmp_path):
    legacy = tmp_path / "legacy"
    destination = tmp_path / "current"
    legacy.mkdir()
    destination.mkdir()
    (legacy / "openwhisper_settings.json").write_text(
        '{"theme": "dark"}',
        encoding="utf-8",
    )

    migrate_legacy_user_data(destination, legacy)
    target = destination / "openwhisper_settings.json"
    assert target.read_text(encoding="utf-8") == '{"theme": "dark"}'

    target.write_text('{"theme": "light"}', encoding="utf-8")
    migrate_legacy_user_data(destination, legacy)
    assert target.read_text(encoding="utf-8") == '{"theme": "light"}'
