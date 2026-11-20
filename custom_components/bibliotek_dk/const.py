CONF_AGENCY = "agency"
CONF_BRANCH_ID = "branchId"
CONF_CONFIG_ENTRY_ID = "config_entry_id"
CONF_HOST = "host"
CONF_MUNICIPALITY = "municipality"
CONF_NAME = "name"
CONF_PINCODE = "pincode"
CONF_SHOW_DEBTS = "show_debts"
CONF_SHOW_LOANS = "show_loans"
CONF_SHOW_ELOANS = "show_eloans"
CONF_SHOW_RESERVATIONS = "show_reservations"
CONF_UPDATE_INTERVAL = "update_interval"
CONF_USER_ID = "user_id"
CREDITS = "J-Lindvig & TermeHansen (https://github.com/TermeHansen/Bibliotek_dk)"

DOMAIN = "bibliotek_dk"

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

# The Biblo app's API (DDF/BFF). Its record endpoint replaces the scraped
# GraphQL details lookup and needs no user session (plan 7.9). The app-type,
# app-version and installation-id headers are client hints the gateway
# validates before it looks at anything else; the API key is the Biblo app's
# own, which is why these requests work without a patron login.
URL_DDF_BASE = "https://api.detdigitalefolkebibliotek.dk"
DDF_APP_TYPE = "mybiblo"
DDF_APP_VERSION = "Android/1.3.1"
DDF_API_KEY = "wnFOwN6DkmQazm_jiahemlIVgWuQ-fgtNSfBRNdun6w"
# A stable, non-identifying installation id: the gateway only requires >= 10
# characters, and the record endpoint does not tie anything to a device.
DDF_INSTALLATION_ID = "bibliotek-dk-homeassistant"

# Agencies that are usable but absent from the API's agency list, so the switch
# would otherwise drop them. Both serve the same borchk login form.
# (agency id, name, host)
EXTRA_LIBRARIES = (
    ("911116", "Býarbókasavnið", "http://www.bbs.fo"),  # Faroe Islands
    ("911130", "Kalaallit Nunaat", "http://www.katak.gl"),  # Greenland
)

# Municipality -> agency-name exceptions. The config flow matches a
# municipality against the agency names by folded substring ("København" is in
# "Københavns Biblioteker"), which resolves 96 of the 98 Danish municipalities.
# These are the rest.
MUNICIPALITY_ALIASES = {
    "Thisted": "Biblioteket i Thy",
}

# HA service to renew one or more physical loans.
SERVICE_RENEW_LOANS = "renew_loans"
ATTR_LOAN_IDS = "loan_ids"


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
