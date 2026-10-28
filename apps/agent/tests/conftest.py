"""Every test gets its own settings directory, so usage events (and the salt) never leak between
tests or into /data/settings."""

import pytest

from src import usage


@pytest.fixture(autouse=True)
def isolated_settings_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("SETTINGS_DIR", str(tmp_path / "settings"))
    usage._reset()
    yield
    usage._reset()
