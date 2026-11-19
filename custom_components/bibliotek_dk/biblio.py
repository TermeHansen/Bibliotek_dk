"""Read digital patron data through DBC's Biblio LMS adapter.

Contract: danskernesdigitalebibliotek/dpl-web, schemas/openapi/biblio-adapter.yaml.
The adapter accepts the same login.bib.dk end-user token as the physical API.
"""

from __future__ import annotations

from datetime import timezone
import logging
from urllib.parse import quote

from dateutil import parser
from requests.exceptions import RequestException

from .models import libraryLoan, libraryReservation, libraryReservationReady

_LOGGER = logging.getLogger(__name__)


class BiblioError(Exception):
    """An incomplete or failed adapter response; keep the last good state."""


def _date(value):
    """Use naive UTC, matching the facade's existing material dates."""
    result = parser.parse(value)
    if result.tzinfo is not None:
        result = result.astimezone(timezone.utc).replace(tzinfo=None)
    return result


class BiblioClient:
    """Read-only client using the facade's timeout/retry-aware transport."""

    def __init__(self, library, base_url):
        self.library = library
        self.base_url = base_url.rstrip("/")
        self._metadata = {}

    def _get(self, path, **params):
        response = self.library._get(
            f"{self.base_url}/v1/{path}",
            headers=self.library.json_header,
            params=params,
        )
        if response.status_code != 200:
            raise BiblioError(f"Biblio {path}: HTTP {response.status_code}")
        data = response.json()
        if not isinstance(data, dict):
            raise BiblioError(f"Biblio {path}: expected an object")
        return data

    def _pages(self, resource, **params):
        """Only publish a list once every page has been fetched successfully."""
        result = []
        seen = set()
        while True:
            data = self._get(resource, limit=250, **params)
            items = data[resource]
            if not isinstance(items, list):
                raise BiblioError(f"Biblio {resource}: expected a list")
            result.extend(items)
            pagination = data["pagination"]
            if not isinstance(pagination, dict):
                raise BiblioError(f"Biblio {resource}: expected pagination object")
            cursor = pagination.get("cursor")
            if not cursor:
                return result
            if not isinstance(cursor, str) or cursor in seen:
                raise BiblioError(f"Biblio {resource}: invalid pagination cursor")
            seen.add(cursor)
            params["cursor"] = cursor

    def _material(self, cls, data):
        material_id = data["material_id"]
        obj = cls({})
        obj.id = data["id"]
        obj.title = data.get("title") or material_id
        obj.creators = data.get("author") or None
        obj.type = {"ebook": "epub", "audiobook": "audiobook"}.get(
            data["material_type"], data["material_type"]
        )
        obj.url = f"https://bibliotek.dk/?q={quote(material_id, safe='')}"
        return obj

    def loans(self):
        result = []
        for data in self._pages("loans", active="true"):
            try:
                obj = self._material(libraryLoan, data)
                obj.loanDate = _date(data["start"])
                obj.expireDate = _date(data["end"])
                # Digital loans cannot be renewed by the physical renewal service.
                obj.renewAble = False
                result.append(obj)
            except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
                _LOGGER.warning("Skipping malformed Biblo loan")
        return result

    def reservations(self):
        waiting, ready = [], []
        for data in self._pages("reservations"):
            try:
                is_ready = bool(data.get("offer_id") and data.get("offer_expires_at"))
                cls = libraryReservationReady if is_ready else libraryReservation
                obj = self._material(cls, data)
                obj.createdDate = _date(data["timestamp"])
                obj.pickupLibrary = "biblo.dk"
                if is_ready:
                    obj.pickupDate = _date(data["offer_expires_at"])
                    obj.reservationNumber = data["offer_id"]
                else:
                    obj.expireDate = _date(data["loan_date"])
            except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
                _LOGGER.warning("Skipping malformed Biblo reservation")
                continue
            # Metadata is optional: its outage must not hide a reservation.
            isbn = data["material_id"]
            try:
                if isbn not in self._metadata:
                    metadata = self._get(f"metadata/{quote(isbn, safe='')}")
                    materials = metadata["materials"]
                    if not isinstance(materials, list):
                        raise BiblioError("Biblio metadata: expected a list")
                    for material in materials:
                        if (
                            not isinstance(material, dict)
                            or not isinstance(material.get("isbn"), str)
                            or not isinstance(
                                material.get("title", ""), (str, type(None))
                            )
                            or not isinstance(material.get("author") or [], list)
                            or not all(
                                isinstance(author, str)
                                for author in (material.get("author") or [])
                            )
                        ):
                            raise BiblioError("Biblio metadata: invalid material")
                    if len(self._metadata) >= 500:
                        self._metadata.clear()
                    self._metadata[isbn] = materials
                for material in self._metadata[isbn]:
                    if material["isbn"] == isbn:
                        obj.title = material.get("title") or obj.title
                        obj.creators = " og ".join(material.get("author") or []) or None
                        break
            except (BiblioError, RequestException, KeyError, TypeError, ValueError):
                pass
            (ready if is_ready else waiting).append(obj)
        return waiting, ready

    def quotas(self):
        quotas = self._get("users/get_loan_quotas")["loan_quotas"]
        if not isinstance(quotas, list):
            raise BiblioError("Biblio quotas: expected a list")
        return quotas
