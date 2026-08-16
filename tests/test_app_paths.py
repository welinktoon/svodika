"""Tests for update-safe per-user application storage."""

from services.app_paths import (
    APP_DATA_ENV,
    LEGACY_APP_DATA_ENV,
    get_app_data_dir,
    get_user_documents_dir,
    migrate_legacy_user_data,
)


def test_explicit_data_directory_is_created(monkeypatch, tmp_path):
    destination = tmp_path / "user-data"
    monkeypatch.setenv(APP_DATA_ENV, str(destination))

    assert get_app_data_dir() == destination.resolve()
    assert destination.is_dir()


def test_windows_data_directory_uses_only_the_product_name(monkeypatch, tmp_path):
    monkeypatch.delenv(APP_DATA_ENV, raising=False)
    monkeypatch.delenv(LEGACY_APP_DATA_ENV, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr("services.app_paths.sys.platform", "win32")

    assert get_app_data_dir() == tmp_path / "Svodika"


def test_windows_documents_uses_the_redirected_known_folder(monkeypatch, tmp_path):
    redirected = tmp_path / "OneDrive" / "Документы"
    monkeypatch.setattr("services.app_paths.sys.platform", "win32")
    monkeypatch.setattr(
        "services.app_paths._windows_documents_dir",
        lambda: redirected,
    )

    assert get_user_documents_dir() == redirected


def test_documents_path_has_a_portable_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr("services.app_paths.sys.platform", "win32")
    monkeypatch.setattr(
        "services.app_paths._windows_documents_dir",
        lambda: None,
    )
    monkeypatch.setattr("services.app_paths.Path.home", lambda: tmp_path)

    assert get_user_documents_dir() == tmp_path / "Documents"


def test_legacy_environment_override_still_works(monkeypatch, tmp_path):
    destination = tmp_path / "legacy-override"
    monkeypatch.delenv(APP_DATA_ENV, raising=False)
    monkeypatch.setenv(LEGACY_APP_DATA_ENV, str(destination))

    assert get_app_data_dir() == destination.resolve()


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


def test_default_migration_reads_the_previous_windows_path(monkeypatch, tmp_path):
    old_data = tmp_path / "Welinkton" / "MeetingRecorder"
    old_data.mkdir(parents=True)
    (old_data / "openwhisper_settings.json").write_text(
        '{"theme": "dark"}',
        encoding="utf-8",
    )
    destination = tmp_path / "Svodika"
    destination.mkdir()
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr("services.app_paths.sys.platform", "win32")

    migrate_legacy_user_data(destination)

    assert (destination / "openwhisper_settings.json").read_text(
        encoding="utf-8"
    ) == '{"theme": "dark"}'
