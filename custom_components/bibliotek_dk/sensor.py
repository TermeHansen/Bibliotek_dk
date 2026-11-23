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
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)

from datetime import timedelta, datetime
from decimal import Decimal
import hashlib

from .const import (
    CONF_EXPOSE_PERSONAL_INFO,
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
    myLibrary.get_loans = new_data.get(CONF_SHOW_LOANS, True)
    myLibrary.get_debts = new_data.get(CONF_SHOW_DEBTS, True)
    myLibrary.get_reservations = new_data.get(CONF_SHOW_RESERVATIONS, True)
    expose_personal_info = bool(new_data.get(CONF_EXPOSE_PERSONAL_INFO, False))
    # Expose the coordinator so services (renew_loans) can request a refresh
    # without reloading the whole config entry.
    myLibrary.coordinator = coordinator

    # Immediate refresh
    await coordinator.async_request_refresh()

    sensors = []

    # Library
    sensors.append(
        LibrarySensor(
            myLibrary,
            coordinator,
            entry_id=entry.entry_id,
            expose_personal_info=expose_personal_info,
        )
    )

    # Loans
    if new_data.get(CONF_SHOW_LOANS, True):
        sensors.append(LoanSensor(myLibrary.user, coordinator, entry_id=entry.entry_id))
        sensors.append(
            LoanOverdueSensor(myLibrary.user, coordinator, entry_id=entry.entry_id)
        )

    # Debts
    if new_data.get(CONF_SHOW_DEBTS, True):
        sensors.append(DebtSensor(myLibrary.user, coordinator, entry_id=entry.entry_id))

    # Reservations
    if new_data.get(CONF_SHOW_RESERVATIONS, True):
        sensors.append(
            ReservationSensor(myLibrary.user, coordinator, entry_id=entry.entry_id)
        )
        sensors.append(
            ReservationReadySensor(myLibrary.user, coordinator, entry_id=entry.entry_id)
        )

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


def _legacy_md5_unique_id(string: str) -> str:
    """Compute historical unsalted MD5 sensor unique IDs solely for v1->v2 migration."""
    return hashlib.md5(string.encode("utf-8"), usedforsecurity=False).hexdigest()


class BibliotekBaseSensor(CoordinatorEntity, SensorEntity):
    """Shared coordinator wiring for all Bibliotek sensors.

    Subclasses set ``self._name`` and ``self._unique_id`` in ``__init__`` (after
    calling ``super().__init__(coordinator)``) and implement ``state`` /
    ``extra_state_attributes``.
    """

    def __init__(self, coordinator: DataUpdateCoordinator) -> None:
        super().__init__(coordinator)

    @property
    def name(self):
        return self._name

    @property
    def unique_id(self):
        return self._unique_id


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
        unique_suffix: str,
        attr_key: str,
        entry_id: str | None = None,
    ) -> None:
        super().__init__(coordinator)
        self.libraryUser = library_user
        self._name = name
        scope = entry_id or library_user.log_label
        self._unique_id = f"{scope}_{unique_suffix}"
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
        entry_id: str | None = None,
        expose_personal_info: bool = False,
    ) -> None:
        super().__init__(coordinator)
        self.myLibrary = myLibrary
        self._expose_personal_info = expose_personal_info
        self._name = f"{self.myLibrary.libraryName} ({self.myLibrary.user.name})"
        scope = entry_id or self.myLibrary.user.log_label
        self._unique_id = f"{scope}_main"

    @property
    def icon(self):
        return "mdi:library"

    @property
    def state(self):
        if len(self.myLibrary.user.loans) > 0:
            # Only calculate days remaining when the earliest loan has a due date.
            if self.myLibrary.user.loans[0].expireDate is not None:
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
            "phone_notifications": self.myLibrary.user.phoneNotify,
            "mail_notifications": self.myLibrary.user.mailNotify,
            "pickup_library": self.myLibrary.user.pickupLibrary,
            "ebooks": self.myLibrary.user.eBooks,
            "ebooks_quota": self.myLibrary.user.eBooksQuota,
            "audiobooks": self.myLibrary.user.audioBooks,
            "audiobooks_quota": self.myLibrary.user.audioBooksQuota,
            "other_digital_loans": self.myLibrary.user.otherDigitalLoans,
            "digital_loans_quota": self.myLibrary.user.digitalLoansQuota,
            "sensor_type": "main",
            ATTR_UNIT_OF_MEASUREMENT: "days",
            ATTR_ATTRIBUTION: CREDITS,
        }
        if self._expose_personal_info:
            attr["address"] = self.myLibrary.user.address
            attr["phone"] = self.myLibrary.user.phone
            attr["mail"] = self.myLibrary.user.mail

        # PNG as icon/entity-picture
        if self.myLibrary.icon:
            attr.update({ATTR_ENTITY_PICTURE: self.myLibrary.icon})

        return attr


class LoanSensor(BibliotekCountSensor):
    _icon_empty = "mdi:book-cancel"
    _icon_single = "mdi:book"
    _icon_many = "mdi:book-multiple"

    def __init__(
        self,
        library_user: libraryUser,
        coordinator: DataUpdateCoordinator,
        entry_id: str | None = None,
    ) -> None:
        super().__init__(
            library_user,
            coordinator,
            name=f"Bibliotekslån ({library_user.name})",
            unique_suffix="loans",
            attr_key="loans",
            entry_id=entry_id,
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
        self,
        library_user: libraryUser,
        coordinator: DataUpdateCoordinator,
        entry_id: str | None = None,
    ) -> None:
        super().__init__(
            library_user,
            coordinator,
            name=f"Bibliotekslån overskredet ({library_user.name})",
            unique_suffix="loans_overdue",
            attr_key="loans_overdue",
            entry_id=entry_id,
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
        self,
        library_user: libraryUser,
        coordinator: DataUpdateCoordinator,
        entry_id: str | None = None,
    ) -> None:
        super().__init__(
            library_user,
            coordinator,
            name=f"Reservationer ({library_user.name})",
            unique_suffix="reservations",
            attr_key="reservations",
            entry_id=entry_id,
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
        self,
        library_user: libraryUser,
        coordinator: DataUpdateCoordinator,
        entry_id: str | None = None,
    ) -> None:
        super().__init__(
            library_user,
            coordinator,
            name=f"Reservationer klar ({library_user.name})",
            unique_suffix="reservations_ready",
            attr_key="reservations_ready",
            entry_id=entry_id,
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
        self,
        library_user: libraryUser,
        coordinator: DataUpdateCoordinator,
        entry_id: str | None = None,
    ) -> None:
        super().__init__(
            library_user,
            coordinator,
            name=f"Gebyrer ({library_user.name})",
            unique_suffix="debts",
            attr_key="debts",
            entry_id=entry_id,
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
