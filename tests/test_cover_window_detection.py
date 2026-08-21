"""Tests for distinguishing VELUX roof windows from shutters."""

from types import SimpleNamespace

from velux_active import cover


def test_reported_type_takes_precedence_over_name():
    module = SimpleNamespace(name="Buero Rolladen Dachfenster", velux_type="shutter")
    assert cover._module_is_window("id1", module) is False


def test_name_is_used_when_type_is_missing():
    assert cover._module_is_window("id1", SimpleNamespace(name="Dachfenster"))


def test_allow_list_overrides_reported_type(monkeypatch):
    monkeypatch.setattr(cover, "WINDOW_MODULE_IDS", {"id1"})
    module = SimpleNamespace(name="Rolluik", velux_type="shutter")
    assert cover._module_is_window("id1", module)
