"""Tests for distinguishing VELUX roof windows from roller shutters."""

from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace

from velux_active import cover


def _module(name: str, velux_type: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(name=name, velux_type=velux_type)


@contextmanager
def _allow_listed(module_id: str) -> Iterator[None]:
    cover.WINDOW_MODULE_IDS.add(module_id)
    try:
        yield
    finally:
        cover.WINDOW_MODULE_IDS.discard(module_id)


def test_reported_window_type_is_a_window():
    assert cover._module_is_window("id1", _module("Bedroom", "window")) is True


def test_reported_shutter_type_wins_over_window_name_keyword():
    """A shutter named after the window it covers must stay a shutter.

    The API reports velux_type "shutter" for these modules, but names such as
    "Buero Rolladen Dachfenster" contain a window keyword. Treating them as
    windows adds an auto-ventilation switch to a shutter, reports the wrong
    cover device class and routes movement through the signed window path.
    """
    module = _module("Buero Rolladen Dachfenster", "shutter")

    assert cover._module_is_window("id1", module) is False


def test_name_keyword_still_used_without_a_reported_type():
    assert cover._module_is_window("id1", _module("Roof Window")) is True
    assert cover._module_is_window("id1", _module("Living room shutter")) is False


def test_empty_reported_type_falls_back_to_the_name():
    assert cover._module_is_window("id1", _module("Dachfenster", "")) is True


def test_allow_list_used_without_a_reported_type():
    with _allow_listed("id1"):
        assert cover._module_is_window("id1", _module("Rolluik")) is True


def test_allow_list_overrides_a_reported_shutter_type():
    """The manual override stays the escape hatch for a wrong API type."""
    with _allow_listed("id1"):
        assert cover._module_is_window("id1", _module("Rolluik", "shutter")) is True


def test_missing_velux_type_attribute_falls_back_to_the_name():
    assert cover._module_is_window("id1", SimpleNamespace(name="Fenster")) is True
