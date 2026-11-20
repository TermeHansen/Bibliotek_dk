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

    class AbortFlow(Exception):
        """Fake AbortFlow raised when config flow aborts early."""

        def __init__(self, reason: str) -> None:
            super().__init__(reason)
            self.reason = reason

    class ConfigFlow:
        """Minimal ConfigFlow accepting the ``domain=`` class kwarg."""

        VERSION = 1

        def __init__(self):
            self.hass = None
            self.context = {}
            self._unique_id = None

        def __init_subclass__(cls, domain=None, **kwargs):
            super().__init_subclass__(**kwargs)
            cls.domain = domain

        async def async_set_unique_id(self, unique_id=None):
            self._unique_id = unique_id

        def _abort_if_unique_id_configured(self):
            entries = getattr(getattr(self.hass, "config_entries", None), "entries", [])
            for entry in entries:
                if getattr(entry, "unique_id", None) == self._unique_id:
                    raise AbortFlow("already_configured")

        def _get_reauth_entry(self):
            entry_id = self.context.get("entry_id")
            for entry in getattr(
                getattr(self.hass, "config_entries", None), "entries", []
            ):
                if entry.entry_id == entry_id:
                    return entry
            return self.context.get("entry")

        def _get_reconfigure_entry(self):
            entry_id = self.context.get("entry_id")
            for entry in getattr(
                getattr(self.hass, "config_entries", None), "entries", []
            ):
                if entry.entry_id == entry_id:
                    return entry
            return self.context.get("entry")

        def async_show_form(self, **kwargs):
            return {"type": "form", **kwargs}

        def async_create_entry(self, **kwargs):
            return {"type": "create_entry", **kwargs}

        def async_abort(self, **kwargs):
            return {"type": "abort", **kwargs}

        def async_update_reload_and_abort(
            self,
            entry,
            *,
            data_updates=None,
            data=None,
            unique_id=None,
            reason="reauth_successful",
            **kwargs,
        ):
            merged = dict(entry.data)
            if data is not None:
                merged = dict(data)
            if data_updates is not None:
                merged.update(data_updates)
            config_entries_mgr = getattr(self.hass, "config_entries", None)
            if config_entries_mgr is not None and hasattr(
                config_entries_mgr, "async_update_entry"
            ):
                update_kwargs = {"data": merged}
                if unique_id is not None:
                    update_kwargs["unique_id"] = unique_id
                config_entries_mgr.async_update_entry(entry, **update_kwargs)
            else:
                entry.data = merged
                if unique_id is not None:
                    entry.unique_id = unique_id
            if config_entries_mgr is not None and hasattr(
                config_entries_mgr, "reloaded"
            ):
                config_entries_mgr.reloaded.append(entry.entry_id)
            return {"type": "abort", "reason": reason, **kwargs}

    class OptionsFlow:
        """Minimal OptionsFlow base."""

        def __init__(self):
            self.hass = None
            self.config_entry = None

        def async_show_form(self, **kwargs):
            return {"type": "form", **kwargs}

        def async_create_entry(self, **kwargs):
            return {"type": "create_entry", **kwargs}

        def add_suggested_values_to_schema(self, data_schema, suggested_values):
            return data_schema

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
    data_entry_flow.AbortFlow = AbortFlow

    exceptions = types.ModuleType("homeassistant.exceptions")

    class HomeAssistantError(Exception):
        """Fake HomeAssistantError."""

    class ConfigEntryAuthFailed(HomeAssistantError):
        """Fake ConfigEntryAuthFailed raised when reauth is required."""

    exceptions.HomeAssistantError = HomeAssistantError
    exceptions.ConfigEntryAuthFailed = ConfigEntryAuthFailed

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

        def __call__(self, value):
            return value

    class TextSelectorType:
        """Fake TextSelectorType enum."""

        PASSWORD = "password"  # noqa: S105
        TEXT = "text"

    class TextSelectorConfig:
        """Fake TextSelectorConfig accepting kwargs."""

        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class TextSelector:
        """Fake TextSelector accepting a config."""

        def __init__(self, config=None):
            self.config = config

        def __call__(self, value):
            return value

    selector = types.ModuleType("homeassistant.helpers.selector")
    selector.SelectSelector = SelectSelector
    selector.SelectSelectorConfig = SelectSelectorConfig
    selector.SelectSelectorMode = SelectSelectorMode
    selector.TextSelector = TextSelector
    selector.TextSelectorConfig = TextSelectorConfig
    selector.TextSelectorType = TextSelectorType
    helpers.selector = selector

    class _FakeRegistryEntry:
        """Minimal entity-registry record."""

        def __init__(
            self,
            entity_id,
            unique_id,
            config_entry_id,
            platform="bibliotek_dk",
            *,
            disabled_by=None,
            created_at=None,
        ):
            self.entity_id = entity_id
            self.unique_id = unique_id
            self.config_entry_id = config_entry_id
            self.platform = platform
            self.disabled_by = disabled_by
            self.created_at = created_at

    class _FakeEntityRegistry:
        """Minimal EntityRegistry stub supporting async_update_entity."""

        def __init__(self):
            self.entities = {}

        def async_update_entity(self, entity_id, *, new_unique_id=None):
            entry = self.entities.get(entity_id)
            if entry is not None and new_unique_id is not None:
                for other in self.entities.values():
                    if (
                        other is not entry
                        and other.platform == entry.platform
                        and other.unique_id == new_unique_id
                    ):
                        raise ValueError(
                            f"Unique id {new_unique_id} is already in use by {other.entity_id}"
                        )
                entry.unique_id = new_unique_id
                return entry
            return None

    entity_registry = types.ModuleType("homeassistant.helpers.entity_registry")
    entity_registry.RegistryEntry = _FakeRegistryEntry
    entity_registry.EntityRegistry = _FakeEntityRegistry
    entity_registry.async_get = lambda hass: getattr(
        hass, "_entity_registry", _FakeEntityRegistry()
    )
    entity_registry.async_entries_for_config_entry = lambda registry, entry_id: [
        entry
        for entry in registry.entities.values()
        if entry.config_entry_id == entry_id
    ]
    helpers.entity_registry = entity_registry

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

    class UpdateFailed(Exception):
        """Fake UpdateFailed raised when a coordinator update fails."""

    update_coordinator.UpdateFailed = UpdateFailed
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
    sys.modules["homeassistant.helpers.entity_registry"] = entity_registry
    sys.modules["homeassistant.helpers.entity_platform"] = entity_platform
    sys.modules["homeassistant.helpers.update_coordinator"] = update_coordinator
    sys.modules["homeassistant.helpers.device_registry"] = device_registry
    sys.modules["homeassistant.helpers.service"] = service
    sys.modules["homeassistant.helpers.typing"] = typing_mod
