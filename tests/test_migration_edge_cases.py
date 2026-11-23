"""Regression tests for v1 -> v2 entity migration edge cases."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import logging
from pathlib import Path

import pytest

from custom_components.bibliotek_dk import async_migrate_entry
from custom_components.bibliotek_dk.const import (
    CONF_AGENCY,
    CONF_HOST,
    CONF_MUNICIPALITY,
    CONF_PINCODE,
    CONF_SHOW_DEBTS,
    CONF_SHOW_LOANS,
    CONF_SHOW_RESERVATIONS,
    CONF_UPDATE_INTERVAL,
    CONF_USER_ID,
)


class _FakeConfig:
    def __init__(self, root: Path) -> None:
        self._root = root

    def path(self, *parts: str) -> str:
        return str(self._root.joinpath(*parts))


class _FakeConfigEntries:
    def __init__(self, entries=None) -> None:
        self.entries = list(entries or [])
        self.updated_calls = []

    def async_update_entry(
        self,
        entry,
        *,
        data=None,
        options=None,
        unique_id=None,
        version=None,
        title=None,
    ):
        if data is not None:
            entry.data = data
        if options is not None:
            entry.options = options
        if unique_id is not None:
            entry.unique_id = unique_id
        if version is not None:
            entry.version = version
        if title is not None:
            entry.title = title
        self.updated_calls.append(entry.entry_id)


class _FakeEntry:
    def __init__(
        self,
        data: dict,
        *,
        entry_id: str = "entry-abc-1234",
        options: dict | None = None,
        unique_id: str | None = None,
        version: int = 1,
        title: str = "Aarhus",
    ) -> None:
        self.entry_id = entry_id
        self.data = data
        self.options = dict(options or {})
        self.unique_id = unique_id
        self.version = version
        self.title = title


class _FakeHass:
    def __init__(self, root: Path, entries=None) -> None:
        from homeassistant.helpers import entity_registry as er

        self.config = _FakeConfig(root)
        self.data = {}
        self.config_entries = _FakeConfigEntries(entries)
        self._entity_registry = er.EntityRegistry()

    async def async_add_executor_job(self, func, *args):
        return func(*args)


def _md5(value: str) -> str:
    return hashlib.md5(value.encode("utf-8"), usedforsecurity=False).hexdigest()


def _is_hex_md5(value: str) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 32
        and all(ch in "0123456789abcdef" for ch in value)
    )


def _make_v1_entry(cpr: str = "0102991234") -> _FakeEntry:
    return _FakeEntry(
        data={
            CONF_USER_ID: cpr,
            CONF_PINCODE: "4321",
            CONF_HOST: "https://test.bib.dk",
            CONF_AGENCY: "710100",
            CONF_MUNICIPALITY: "Test Kommune",
            CONF_SHOW_LOANS: True,
            CONF_SHOW_DEBTS: True,
            CONF_SHOW_RESERVATIONS: True,
            CONF_UPDATE_INTERVAL: 60,
        },
        unique_id=cpr,
        version=1,
    )


@pytest.mark.asyncio
async def test_migrate_multiple_historical_pickup_branch_sensors(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    """Multiple enabled historical pickup-branch MD5 sensors migrate without collision."""
    from homeassistant.helpers import entity_registry as er

    cpr = "0102991234"
    hass = _FakeHass(tmp_path)
    entry = _make_v1_entry(cpr)
    hass.config_entries.entries.append(entry)

    reg = er.async_get(hass)
    reg.entities = {
        "sensor.branch_old_1": er.RegistryEntry(
            "sensor.branch_old_1", _md5("Dokk1" + cpr), entry.entry_id
        ),
        "sensor.branch_old_2": er.RegistryEntry(
            "sensor.branch_old_2", _md5("Viby" + cpr), entry.entry_id
        ),
        "sensor.branch_newest": er.RegistryEntry(
            "sensor.branch_newest", _md5("Hovedbiblioteket" + cpr), entry.entry_id
        ),
    }

    with caplog.at_level(logging.WARNING):
        assert await async_migrate_entry(hass, entry) is True

    assert entry.version == 2
    assert reg.entities["sensor.branch_newest"].unique_id == f"{entry.entry_id}_main"
    assert (
        reg.entities["sensor.branch_old_1"].unique_id
        == f"{entry.entry_id}_legacy_main_1"
    )
    assert (
        reg.entities["sensor.branch_old_2"].unique_id
        == f"{entry.entry_id}_legacy_main_2"
    )
    assert not any(_is_hex_md5(ent.unique_id) for ent in reg.entities.values())
    assert any(
        record.levelno == logging.WARNING and entry.entry_id in record.message
        for record in caplog.records
    )
    assert all(cpr not in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_migrate_prefers_enabled_over_disabled_newer_sensor(tmp_path: Path):
    """Enabled older branch sensor claims _main over disabled newer branch sensor."""
    from homeassistant.helpers import entity_registry as er

    cpr = "0102991234"
    hass = _FakeHass(tmp_path)
    entry = _make_v1_entry(cpr)
    hass.config_entries.entries.append(entry)

    reg = er.async_get(hass)
    reg.entities = {
        "sensor.branch_enabled_older": er.RegistryEntry(
            "sensor.branch_enabled_older",
            _md5("Viby" + cpr),
            entry.entry_id,
            disabled_by=None,
            created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        ),
        "sensor.branch_disabled_newer": er.RegistryEntry(
            "sensor.branch_disabled_newer",
            _md5("Hovedbiblioteket" + cpr),
            entry.entry_id,
            disabled_by="user",
            created_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        ),
    }

    assert await async_migrate_entry(hass, entry) is True
    assert (
        reg.entities["sensor.branch_enabled_older"].unique_id
        == f"{entry.entry_id}_main"
    )
    assert (
        reg.entities["sensor.branch_disabled_newer"].unique_id
        == f"{entry.entry_id}_legacy_main_1"
    )


@pytest.mark.asyncio
async def test_migrate_selects_newest_created_at_when_out_of_registry_order(
    tmp_path: Path,
):
    """Newest created_at enabled branch sensor claims _main even if earlier in registry."""
    from homeassistant.helpers import entity_registry as er

    cpr = "0102991234"
    hass = _FakeHass(tmp_path)
    entry = _make_v1_entry(cpr)
    hass.config_entries.entries.append(entry)

    reg = er.async_get(hass)
    reg.entities = {
        "sensor.branch_newest_first": er.RegistryEntry(
            "sensor.branch_newest_first",
            _md5("Hovedbiblioteket" + cpr),
            entry.entry_id,
            created_at=datetime(2025, 6, 1, tzinfo=timezone.utc),
        ),
        "sensor.branch_older_second": er.RegistryEntry(
            "sensor.branch_older_second",
            _md5("Viby" + cpr),
            entry.entry_id,
            created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        ),
    }

    assert await async_migrate_entry(hass, entry) is True
    assert (
        reg.entities["sensor.branch_newest_first"].unique_id == f"{entry.entry_id}_main"
    )
    assert (
        reg.entities["sensor.branch_older_second"].unique_id
        == f"{entry.entry_id}_legacy_main_1"
    )


@pytest.mark.asyncio
async def test_migrate_preserves_existing_main_and_renames_legacy_md5s(tmp_path: Path):
    """Existing <entry_id>_main stays untouched while legacy MD5 main sensors get _legacy_main_N."""
    from homeassistant.helpers import entity_registry as er

    cpr = "0102991234"
    hass = _FakeHass(tmp_path)
    entry = _make_v1_entry(cpr)
    hass.config_entries.entries.append(entry)

    reg = er.async_get(hass)
    reg.entities = {
        "sensor.current_main": er.RegistryEntry(
            "sensor.current_main", f"{entry.entry_id}_main", entry.entry_id
        ),
        "sensor.legacy_1": er.RegistryEntry(
            "sensor.legacy_1", _md5("Dokk1" + cpr), entry.entry_id
        ),
        "sensor.legacy_2": er.RegistryEntry(
            "sensor.legacy_2", _md5("Viby" + cpr), entry.entry_id
        ),
    }

    assert await async_migrate_entry(hass, entry) is True
    assert reg.entities["sensor.current_main"].unique_id == f"{entry.entry_id}_main"
    assert (
        reg.entities["sensor.legacy_1"].unique_id == f"{entry.entry_id}_legacy_main_1"
    )
    assert (
        reg.entities["sensor.legacy_2"].unique_id == f"{entry.entry_id}_legacy_main_2"
    )


@pytest.mark.asyncio
async def test_migrate_handles_existing_loans_alongside_legacy_md5_loans(
    tmp_path: Path,
):
    """Existing <entry_id>_loans stays untouched while legacy MD5 Loans_ becomes _legacy_loans_1."""
    from homeassistant.helpers import entity_registry as er

    cpr = "0102991234"
    hass = _FakeHass(tmp_path)
    entry = _make_v1_entry(cpr)
    hass.config_entries.entries.append(entry)

    reg = er.async_get(hass)
    reg.entities = {
        "sensor.current_loans": er.RegistryEntry(
            "sensor.current_loans", f"{entry.entry_id}_loans", entry.entry_id
        ),
        "sensor.legacy_loans": er.RegistryEntry(
            "sensor.legacy_loans", _md5("Loans_" + cpr), entry.entry_id
        ),
    }

    assert await async_migrate_entry(hass, entry) is True
    assert reg.entities["sensor.current_loans"].unique_id == f"{entry.entry_id}_loans"
    assert (
        reg.entities["sensor.legacy_loans"].unique_id
        == f"{entry.entry_id}_legacy_loans_1"
    )
