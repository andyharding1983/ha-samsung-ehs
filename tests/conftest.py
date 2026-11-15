"""Load the pure-logic modules without importing Home Assistant."""

import importlib.util
import pathlib
import sys

PKG = pathlib.Path(__file__).parents[1] / "custom_components" / "samsung_ehs"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"ehs_{name}", PKG / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


registers = _load("registers")
weather_comp = _load("weather_comp")
cop = _load("cop")


try:  # Home Assistant test harness is optional for the pure-logic tests
    import pytest
    import pytest_homeassistant_custom_component  # noqa: F401

    @pytest.fixture(autouse=True)
    def auto_enable_custom_integrations(enable_custom_integrations):
        yield
except ImportError:  # pragma: no cover
    pass
