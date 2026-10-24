from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from datetime import timedelta, datetime
from decimal import Decimal
import hashlib

from .const import (
    CONF_UPDATE_INTERVAL,
    CREDITS,
    DOMAIN,
    CONF_SHOW_LOANS,
    CONF_SHOW_DEBTS,
    CONF_SHOW_RESERVATIONS,
)
from homeassistant.const import (
    ATTR_ATTRIBUTION,
    ATTR_UNIT_OF_MEASUREMENT,
    ATTR_ENTITY_PICTURE,
)

from .library_api import Library, LibraryAuthError
from .models import libraryUser

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:

    # Define a update function
    async def async_update_data():
        # Retrieve the client stored in the hass data stack
        myLibrary = hass.data[DOMAIN][entry.entry_id]
        # Call, and wait for it to finish, the function with the refresh procedure
        try:
            await hass.async_add_executor_job(myLibrary.update)
        except LibraryAuthError as err:
            # Surface a failed login (bad credentials, blocked account, ...)
            # as an update failure so the sensors go unavailable instead of
            # silently serving stale data.
            raise UpdateFailed(err) from err

    # Create a coordinator
    new_data = {**entry.data, **entry.options}
    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name="sensor",
        update_method=async_update_data,
        update_interval=timedelta(minutes=int(new_data[CONF_UPDATE_INTERVAL])),
    )
    myLibrary = hass.data[DOMAIN][entry.entry_id]
    myLibrary.get_loans = new_data[CONF_SHOW_LOANS]
    myLibrary.get_depts = new_data[CONF_SHOW_DEBTS]
    myLibrary.get_reservations = new_data[CONF_SHOW_RESERVATIONS]
    # Expose the coordinator so services (renew_loans) can request a refresh
    # without reloading the whole config entry.
    myLibrary.coordinator = coordinator

    # Immediate refresh
    await coordinator.async_request_refresh()

    sensors = []

    # Library
    sensors.append(LibrarySensor(myLibrary, coordinator))

    # Loans
    if new_data[CONF_SHOW_LOANS]:
        sensors.append(LoanSensor(myLibrary.user, coordinator))
        sensors.append(LoanOverdueSensor(myLibrary.user, coordinator))

    # Debts
    if new_data[CONF_SHOW_DEBTS]:
        sensors.append(DebtSensor(myLibrary.user, coordinator))

    # Reservations
    if new_data[CONF_SHOW_RESERVATIONS]:
        sensors.append(ReservationSensor(myLibrary.user, coordinator))
        sensors.append(ReservationReadySensor(myLibrary.user, coordinator))

    # Group every sensor under a single device per config entry.
    device_info = DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        entry_type=DeviceEntryType.SERVICE,
        name=entry.title,
        manufacturer="DBC / bibliotek.dk",
        model=myLibrary.libraryName,
    )
    for sensor in sensors:
        sensor._attr_device_info = device_info

    async_add_entities(sensors)


def md5_unique_id(string):
    return hashlib.md5(string.encode("utf-8"), usedforsecurity=False).hexdigest()


class BibliotekBaseSensor(SensorEntity):
    """Shared coordinator wiring for all Bibliotek sensors.

    Subclasses set ``self._name`` and ``self._unique_id`` in ``__init__`` (after
    calling ``super().__init__(coordinator)``) and implement ``state`` /
    ``extra_state_attributes``.
    """

    def __init__(self, coordinator: DataUpdateCoordinator) -> None:
        self.coordinator = coordinator

    @property
    def name(self):
        return self._name

    @property
    def unique_id(self):
        return self._unique_id

    @property
    def should_poll(self):
        """No need to poll. Coordinator notifies entity of updates."""
        return False

    @property
    def available(self):
        """Return if entity is available."""
        return self.coordinator.last_update_success

    async def async_update(self):
        """Update the entity. Only used by the generic entity update service."""
        await self.coordinator.async_request_refresh()

    async def async_added_to_hass(self):
        """When entity is added to hass."""
        self.async_on_remove(
            self.coordinator.async_add_listener(self.async_write_ha_state)
        )


class BibliotekCountSensor(BibliotekBaseSensor):
    """Base for sensors whose state is the number of items in a list.

    Subclasses provide ``_items`` and ``_item_attrs`` plus the icon variants.
    """

    _icon_empty = "mdi:book-cancel"
    _icon_single = "mdi:book"
    _icon_many = "mdi:book-multiple"

    def __init__(
        self,
        library_user: libraryUser,
        coordinator: DataUpdateCoordinator,
        name: str,
        unique_prefix: str,
        attr_key: str,
    ) -> None:
        super().__init__(coordinator)
        self.libraryUser = library_user
        self._name = name
        self._unique_id = md5_unique_id(unique_prefix + library_user.userId)
        self._attr_key = attr_key

    @property
    def _items(self):
        raise NotImplementedError

    def _item_attrs(self, item) -> dict:
        raise NotImplementedError

    @property
    def icon(self):
        amount = len(self._items)
        if amount == 0:
            return self._icon_empty
        if amount == 1:
            return self._icon_single
        return self._icon_many

    @property
    def state(self):
        return len(self._items)

    @property
    def extra_state_attributes(self):
        attr = {"user": self.libraryUser.name}
        attr[self._attr_key] = [self._item_attrs(item) for item in self._items]
        attr[ATTR_ATTRIBUTION] = CREDITS
        return attr


class LibrarySensor(BibliotekBaseSensor):
    def __init__(
        self,
        myLibrary: Library,
        coordinator: DataUpdateCoordinator,
    ) -> None:
        super().__init__(coordinator)
        self.myLibrary = myLibrary
        self._name = f"{self.myLibrary.libraryName} ({self.myLibrary.user.name})"
        self._unique_id = md5_unique_id(
            self.myLibrary.libraryName + self.myLibrary.user.userId
        )

    @property
    def icon(self):
        return "mdi:library"

    @property
    def state(self):
        if len(self.myLibrary.user.loans) > 0:
            if (
                self.myLibrary.user.loans[0].expireDate is not None
            ):  # eReolen reservation in Queue
                return (
                    self.myLibrary.user.loans[0].expireDate.date()
                    - datetime.now().date()
                ).days
        return ""

    @property
    def extra_state_attributes(self):
        attr = {
            "loans": len(self.myLibrary.user.loans),
            "loans_overdue": len(self.myLibrary.user.loansOverdue),
            "reservations": len(self.myLibrary.user.reservations),
            "reservations_ready": len(self.myLibrary.user.reservationsReady),
            "debts": len(self.myLibrary.user.debts),
            "user": self.myLibrary.user.name,
            "address": self.myLibrary.user.address,
            "phone": self.myLibrary.user.phone,
            "phone_notifications": self.myLibrary.user.phoneNotify,
            "mail": self.myLibrary.user.mail,
            "mail_notifications": self.myLibrary.user.mailNotify,
            "pickup_library": self.myLibrary.user.pickupLibrary,
            "ebooks": self.myLibrary.user.eBooks,
            "ebooks_quota": self.myLibrary.user.eBooksQuota,
            "audiobooks": self.myLibrary.user.audioBooks,
            "audiobooks_quota": self.myLibrary.user.audioBooksQuota,
            "sensor_type": "main",
            ATTR_UNIT_OF_MEASUREMENT: "days",
            ATTR_ATTRIBUTION: CREDITS,
        }

        # PNG as icon/entity-picture
        if self.myLibrary.icon:
            attr.update({ATTR_ENTITY_PICTURE: self.myLibrary.icon})

        return attr


class LoanSensor(BibliotekCountSensor):
    _icon_empty = "mdi:book-cancel"
    _icon_single = "mdi:book"
    _icon_many = "mdi:book-multiple"

    def __init__(
        self, library_user: libraryUser, coordinator: DataUpdateCoordinator
    ) -> None:
        super().__init__(
            library_user,
            coordinator,
            name=f"Bibliotekslån ({library_user.name})",
            unique_prefix="Loans_",
            attr_key="loans",
        )

    @property
    def _items(self):
        return self.libraryUser.loans

    def _item_attrs(self, loan) -> dict:
        return {
            "title": loan.title,
            "creators": loan.creators,
            "type": loan.type,
            "loan_date": loan.loanDate,
            "expire_date": loan.expireDate,
            "renewable": loan.renewAble,
            # The API loan id, needed by the renew_loans service.
            "renew_id": loan.renewId,
            "url": loan.url,
            "cover": loan.coverUrl,
        }


class LoanOverdueSensor(BibliotekCountSensor):
    _icon_empty = "mdi:alarm-off"
    _icon_single = "mdi:alarm"
    _icon_many = "mdi:alarm-multiple"

    def __init__(
        self, library_user: libraryUser, coordinator: DataUpdateCoordinator
    ) -> None:
        super().__init__(
            library_user,
            coordinator,
            name=f"Bibliotekslån overskredet ({library_user.name})",
            unique_prefix="LoansOverdue_",
            attr_key="loans_overdue",
        )

    @property
    def _items(self):
        return self.libraryUser.loansOverdue

    def _item_attrs(self, loan) -> dict:
        return {
            "title": loan.title,
            "creators": loan.creators,
            "type": loan.type,
            "loan_date": loan.loanDate,
            "expire_date": loan.expireDate,
            "url": loan.url,
            "cover": loan.coverUrl,
        }


class ReservationSensor(BibliotekCountSensor):
    _icon_empty = "mdi:book-cancel"
    _icon_single = "mdi:book-plus"
    _icon_many = "mdi:book-plus-multiple"

    def __init__(
        self, library_user: libraryUser, coordinator: DataUpdateCoordinator
    ) -> None:
        super().__init__(
            library_user,
            coordinator,
            name=f"Reservationer ({library_user.name})",
            unique_prefix="Reservations_",
            attr_key="reservations",
        )

    @property
    def _items(self):
        return self.libraryUser.reservations

    def _item_attrs(self, reservation) -> dict:
        return {
            "title": reservation.title,
            "creators": reservation.creators,
            "type": reservation.type,
            "queue_number": reservation.queueNumber,
            "created_date": reservation.createdDate,
            "expire_date": reservation.expireDate,
            "pickup_library": reservation.pickupLibrary,
            "url": reservation.url,
            "cover": reservation.coverUrl,
        }


class ReservationReadySensor(BibliotekCountSensor):
    _icon_empty = "mdi:book-cancel"
    _icon_single = "mdi:book-plus"
    _icon_many = "mdi:book-plus-multiple"

    def __init__(
        self, library_user: libraryUser, coordinator: DataUpdateCoordinator
    ) -> None:
        super().__init__(
            library_user,
            coordinator,
            name=f"Reservationer klar ({library_user.name})",
            unique_prefix="ReservationsReady_",
            attr_key="reservations_ready",
        )

    @property
    def _items(self):
        return self.libraryUser.reservationsReady

    def _item_attrs(self, reservation) -> dict:
        return {
            "title": reservation.title,
            "creators": reservation.creators,
            "type": reservation.type,
            "reservation_number": reservation.reservationNumber,
            "created_date": reservation.createdDate,
            "pickup_date": reservation.pickupDate,
            "pickup_library": reservation.pickupLibrary,
            "url": reservation.url,
            "cover": reservation.coverUrl,
        }


class DebtSensor(BibliotekCountSensor):
    _icon_empty = "mdi:numeric-0"
    _icon_single = "mdi:cash"
    _icon_many = "mdi:cash-multiple"

    # Report the outstanding total as a monetary amount (DKK).
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_native_unit_of_measurement = "DKK"
    _attr_state_class = SensorStateClass.TOTAL

    def __init__(
        self, library_user: libraryUser, coordinator: DataUpdateCoordinator
    ) -> None:
        super().__init__(
            library_user,
            coordinator,
            name=f"Gebyrer ({library_user.name})",
            unique_prefix="Debts_",
            attr_key="debts",
        )

    @property
    def _items(self):
        return self.libraryUser.debts

    @property
    def state(self):
        return self.libraryUser.debtsAmount

    @property
    def native_value(self):
        # Monetary sensors report a Decimal so HA does not render float noise.
        return Decimal(str(self.libraryUser.debtsAmount))

    def _item_attrs(self, debt) -> dict:
        return {
            "title": debt.title,
            "type": debt.type,
            "fee_date": debt.feeDate,
            "fee_type": debt.feeType,
            "fee_amount": debt.feeAmount,
            "url": debt.url,
            "cover": debt.coverUrl,
        }
