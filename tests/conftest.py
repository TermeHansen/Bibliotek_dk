# tests/conftest.py
"""Test bootstrap.

Stubs the ``homeassistant`` package so the integration modules can be imported
and unit-tested without a full Home Assistant install (mirrors the approach in
the sibling ``affalddk`` repo). Only the small surface the integration actually
touches is faked.
"""

from __future__ import annotations

import pathlib
import sys
import types

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

if "homeassistant" not in sys.modules:
    homeassistant = types.ModuleType("homeassistant")
    homeassistant.__path__ = []

    Platform = types.SimpleNamespace(SENSOR="sensor")

    class ConfigFlow:
        """Minimal ConfigFlow accepting the ``domain=`` class kwarg."""

        VERSION = 1

        def __init_subclass__(cls, domain=None, **kwargs):
            super().__init_subclass__(**kwargs)
            cls.domain = domain

    class OptionsFlow:
        """Minimal OptionsFlow base."""

    config_entries = types.ModuleType("homeassistant.config_entries")
    config_entries.ConfigEntry = object
    config_entries.ConfigFlow = ConfigFlow
    config_entries.OptionsFlow = OptionsFlow

    core = types.ModuleType("homeassistant.core")

    class HomeAssistant:
        """Fake HomeAssistant base."""

    class ServiceCall:
        """Fake ServiceCall carrying a data dict."""

        def __init__(self, data=None):
            self.data = data or {}

    class SupportsResponse:
        """Fake SupportsResponse enum."""

        ONLY = "only"
        OPTIONAL = "optional"

    core.HomeAssistant = HomeAssistant
    core.ServiceCall = ServiceCall
    core.SupportsResponse = SupportsResponse
    core.callback = lambda func: func

    data_entry_flow = types.ModuleType("homeassistant.data_entry_flow")
    data_entry_flow.FlowResult = dict

    exceptions = types.ModuleType("homeassistant.exceptions")
    exceptions.HomeAssistantError = Exception

    homeassistant.const = types.SimpleNamespace(
        Platform=Platform,
        ATTR_ATTRIBUTION="attribution",
        ATTR_UNIT_OF_MEASUREMENT="unit_of_measurement",
        ATTR_ENTITY_PICTURE="entity_picture",
    )

    # homeassistant.components.sensor
    components = types.ModuleType("homeassistant.components")
    components.__path__ = []

    class SensorEntity:
        """Fake SensorEntity base."""

    class SensorDeviceClass:
        """Fake SensorDeviceClass enum."""

        MONETARY = "monetary"

    class SensorStateClass:
        """Fake SensorStateClass enum."""

        TOTAL = "total"

    sensor_mod = types.ModuleType("homeassistant.components.sensor")
    sensor_mod.SensorEntity = SensorEntity
    sensor_mod.SensorDeviceClass = SensorDeviceClass
    sensor_mod.SensorStateClass = SensorStateClass
    components.sensor = sensor_mod

    # homeassistant.components.diagnostics
    def async_redact_data(data, to_redact):
        """Recursively replace values of keys in ``to_redact`` (mirrors HA)."""

        def _redact(obj):
            if isinstance(obj, dict):
                return {
                    key: ("**REDACTED**" if key in to_redact else _redact(value))
                    for key, value in obj.items()
                }
            if isinstance(obj, list):
                return [_redact(item) for item in obj]
            return obj

        return _redact(data)

    diagnostics_mod = types.ModuleType("homeassistant.components.diagnostics")
    diagnostics_mod.async_redact_data = async_redact_data
    components.diagnostics = diagnostics_mod

    # homeassistant.helpers.*
    helpers = types.ModuleType("homeassistant.helpers")
    helpers.__path__ = []

    class SelectSelectorMode:
        """Fake SelectSelectorMode enum."""

        DROPDOWN = "dropdown"

    class SelectSelectorConfig:
        """Fake SelectSelectorConfig accepting kwargs."""

        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class SelectSelector:
        """Fake SelectSelector accepting a config."""

        def __init__(self, config=None):
            self.config = config

    selector = types.ModuleType("homeassistant.helpers.selector")
    selector.SelectSelector = SelectSelector
    selector.SelectSelectorConfig = SelectSelectorConfig
    selector.SelectSelectorMode = SelectSelectorMode
    helpers.selector = selector

    entity_platform = types.ModuleType("homeassistant.helpers.entity_platform")
    entity_platform.AddEntitiesCallback = object
    helpers.entity_platform = entity_platform

    # homeassistant.helpers.device_registry
    class DeviceEntryType:
        """Fake DeviceEntryType enum."""

        SERVICE = "service"

    class DeviceInfo(dict):
        """Fake DeviceInfo accepting the documented kwargs as a dict."""

        def __init__(self, **kwargs):
            super().__init__(**kwargs)

    device_registry = types.ModuleType("homeassistant.helpers.device_registry")
    device_registry.DeviceEntryType = DeviceEntryType
    device_registry.DeviceInfo = DeviceInfo
    helpers.device_registry = device_registry

    # homeassistant.helpers.service
    class ServiceCall:
        """Fake ServiceCall with a data mapping."""

        def __init__(self, data=None):
            self.data = data or {}

    service = types.ModuleType("homeassistant.helpers.service")
    service.ServiceCall = ServiceCall
    helpers.service = service

    # homeassistant.helpers.typing
    typing_mod = types.ModuleType("homeassistant.helpers.typing")
    typing_mod.ServiceCall = ServiceCall
    helpers.typing = typing_mod

    update_coordinator = types.ModuleType("homeassistant.helpers.update_coordinator")
    update_coordinator.DataUpdateCoordinator = object
    helpers.update_coordinator = update_coordinator

    homeassistant.helpers = helpers

    homeassistant.config_entries = config_entries
    homeassistant.core = core
    homeassistant.data_entry_flow = data_entry_flow
    homeassistant.exceptions = exceptions
    homeassistant.components = components

    sys.modules["homeassistant"] = homeassistant
    sys.modules["homeassistant.config_entries"] = config_entries
    sys.modules["homeassistant.core"] = core
    sys.modules["homeassistant.data_entry_flow"] = data_entry_flow
    sys.modules["homeassistant.exceptions"] = exceptions
    sys.modules["homeassistant.const"] = homeassistant.const
    sys.modules["homeassistant.components"] = components
    sys.modules["homeassistant.components.sensor"] = sensor_mod
    sys.modules["homeassistant.components.diagnostics"] = diagnostics_mod
    sys.modules["homeassistant.helpers"] = helpers
    sys.modules["homeassistant.helpers.selector"] = selector
    sys.modules["homeassistant.helpers.entity_platform"] = entity_platform
    sys.modules["homeassistant.helpers.update_coordinator"] = update_coordinator
    sys.modules["homeassistant.helpers.device_registry"] = device_registry
    sys.modules["homeassistant.helpers.service"] = service
    sys.modules["homeassistant.helpers.typing"] = typing_mod
