CONF_AGENCY = "agency"
CONF_BRANCH_ID = "branchId"
CONF_CONFIG_ENTRY_ID = "config_entry_id"
CONF_ENCRYPTION_KEY = "encryption_key"
CONF_ENCRYPTION_MODE = "encryption_mode"
CONF_ENCRYPTION_PASSPHRASE = "encryption_passphrase"  # noqa: S105
CONF_EXPOSE_PERSONAL_INFO = "expose_personal_info"
CONF_HOST = "host"
CONF_KEY_FILE = "key_file"
CONF_MUNICIPALITY = "municipality"
CONF_NAME = "name"
CONF_PINCODE = "pincode"
CONF_SHOW_DEBTS = "show_debts"
CONF_SHOW_LOANS = "show_loans"
CONF_SHOW_ELOANS = "show_eloans"
CONF_SHOW_RESERVATIONS = "show_reservations"
CONF_UPDATE_INTERVAL = "update_interval"
CONF_USE_ESCROW_PASSPHRASE = "use_escrow_passphrase"  # noqa: S105
CONF_USER_ID = "user_id"
CREDITS = "J-Lindvig & TermeHansen (https://github.com/TermeHansen/Bibliotek_dk)"

DOMAIN = "bibliotek_dk"

ENCRYPTION_MODE_KEYFILE = "keyfile"
ENCRYPTION_MODE_PASSPHRASE = "passphrase"  # noqa: S105
ENV_ENCRYPTION_KEY = "BIBLIOTEK_DK_ENCRYPTION_KEY"
DATA_CONFIG = f"{DOMAIN}_config"
DATA_ESCROW_PASSPHRASE = f"{DOMAIN}_escrow_passphrase"  # noqa: S105
DATA_PASSPHRASES = f"{DOMAIN}_passphrases"
DATA_MASTER_KEY = f"{DOMAIN}_master_key"
KEY_FILE_NAME = "bibliotek_dk.key"

OPTION_KEYS = (
    CONF_SHOW_LOANS,
    CONF_SHOW_ELOANS,
    CONF_SHOW_DEBTS,
    CONF_SHOW_RESERVATIONS,
    CONF_EXPOSE_PERSONAL_INFO,
    CONF_UPDATE_INTERVAL,
)

HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9",
    # Only advertise encodings `requests` can decode out of the box. Advertising
    # `br`/`zstd` makes servers return content we cannot decode unless the
    # optional brotli/zstandard packages happen to be installed.
    "Accept-Encoding": "gzip, deflate",
    "Accept-Language": "da,en-US;q=0.9,en;q=0.8",
    "Dnt": "1",
    "Upgrade-Insecure-Requests": "1",
    "Referer": "https://www.google.dk/",
}

JSON_HEADERS = {
    "Accept-Encoding": "gzip, deflate",
    "Content-Type": "application/json",
    "Accept": "*/*",
}

# api.dataforsyningen.dk (DAWA) was retired 2026-10-01; the
# dawa.companydata.dk mirror serves the same kommuner/reverse lookup.
MUNICIPALITY_LOOKUP_URL = "https://dawa.companydata.dk/kommuner/reverse?x=LON&y=LAT"

UPDATE_INTERVAL = 60
URL_FALLBACK = "https://bibliotek.kk.dk"
URL_LOGIN = "/login"
URL_LOGIN_PAGE = "/login?current-path=/user/me/dashboard"

# DBC open-platform endpoints (agencyid/patronid are literal placeholders the
# gateway resolves from the bearer token).
URL_FBS_BASE = "https://fbs-openplatform.dbc.dk"
URL_PATRON = f"{URL_FBS_BASE}/external/agencyid/patrons/patronid/v4"
URL_LOANS = f"{URL_FBS_BASE}/external/agencyid/patrons/patronid/loans/v2"
URL_LOANS_RENEW = f"{URL_FBS_BASE}/external/agencyid/patrons/patronid/loans/renew/v2"
URL_RESERVATIONS = (
    f"{URL_FBS_BASE}/external/v1/agencyid/patrons/patronid/reservations/v2"
)
URL_FEES = f"{URL_FBS_BASE}/external/agencyid/patron/patronid/fees/v2"
URL_PUBHUB_BASE = "https://pubhub-openplatform.dbc.dk/v1"
URL_BRANCH_GRAPHQL = "https://bibliotek.dk/api/bibdk21/graphql"

# HA services.
SERVICE_RENEW_LOANS = "renew_loans"
SERVICE_UNLOCK = "unlock"
ATTR_LOAN_IDS = "loan_ids"


details_query = """
    query getManifestationViaMaterialByFaust($faust: String!) {
  manifestation(faust: $faust) {
    ...ManifestationBasicDetails
  }
}

    fragment ManifestationBasicDetails on Manifestation {
  pid
  ownerWork {
    workId
  }
  titles {
    full
  }
  creators {
    display
  }
  cover {
    thumbnail
  }
  materialTypes {
    materialTypeSpecific {
      display
    }
  }
}
"""

branch_query = """
query LibraryFragmentsSearch(
    $q: String,
    $limit: PaginationLimitScalar,
    $offset: Int,
    $language: LanguageCodeEnum,
    $agencyId: String,
    $agencyTypes: [AgencyTypeEnum!]
) {
  branches(
    q: $q,
    agencyid: $agencyId,
    language: $language,
    limit: $limit,
    offset: $offset,
    bibdkExcludeBranches: true,
    statuses: AKTIVE,
    agencyTypes: $agencyTypes
  ) {
    hitcount
    agencyUrl
    result {
      agencyName
      agencyId
      agencyType
      branchId
      name
    }
  }
}
"""
