# Biblo support

## Connection

Biblo is the new Danish library app. Its distribution backend is Biblio LMS;
the similar names refer to different parts of the system. The integration
reads patron data through DBC's Biblio adapter, as the library websites do.
No WeDoBooks API key or reader/player sign-in is needed to read patron data.

The library's `/user/me/loans` page advertises:

* `data-biblio-base-url`: adapter base URL.
* `data-use-biblio-adapter-config="1"`: use the adapter for digital materials.

Both were present on Biblioteket Sønderborg's public page on 2026-11-19;
the base URL was `https://biblio-adapter.dbc.dk`.
An adapter URL alone does not enable the connection. The existing
`show_eloans` setting controls both providers. Reload the integration after
a library changes provider; the existing API URL cache lasts for the account
instance's lifetime.

Read requests send the existing login.bib.dk user bearer token:

* `GET /v1/loans?active=true`: active digital loans, including title and author.
* `GET /v1/reservations`: waiting reservations and available offers.
* `GET /v1/metadata/{material_id}`: optional reservation title/author lookup.
* `GET /v1/users/get_loan_quotas`: organization-specific quotas.

Loan and reservation pagination follows the returned cursor. A failed or
malformed page keeps the previous complete source list. Metadata failure
keeps the reservation with its ISBN as a fallback title. Metadata is cached
with a 500-entry bound. Malformed individual loans/reservations are skipped
with a warning after every page has been fetched; valid items still update
(see `IMPROVEMENT_PLAN.md`, section 7.2). Metadata is validated before caching;
invalid responses are retried on the next lookup (section 3).
Physical library requests and renewal are unchanged.

## Quotas and limitations

Active e-book and audiobook counts come from the new loan list. For one
organization with separate format allowances, the existing `ebooks_quota`
and `audiobooks_quota` attributes show concurrent limits. Shared limits or
multiple organizations cannot be represented by these two fields, so those
fields are `null`. The general sensor's `digital_loans_quota` attribute
contains the organization quotas, including monthly and concurrent limits.
Flattened usage attributes are documented in the README. They read
`current_concurrent_loans` and `current_monthly_loans` from the quota response,
not the active-loan list. Despite its name, `current_monthly_loans` is documented
as consumption within the configured period; monthly aliases therefore require
an explicit one-month period. Podcast period information may be absent.

Waiting reservations expose optional `queue_position` as `queue_number`;
markets that omit it retain `null` and sort after numbered reservations. Available
offers appear in the ready-reservations sensor with their expiry deadline.
Digital materials have bibliography search links but this implementation
does not yet resolve cover images. It does not stream, borrow, cancel,
accept an offer, or renew digital materials.

### Existing dashboard templates

The loan attribute names remain unchanged. Biblio material types include
`audiobook` and `podcast`; e-books are mapped to the existing `epub` value.
The `podcast` value was inferred from the live dashboard's unknown-type error;
it was not independently confirmed from a saved adapter payload and is not
in the documented enum. Unknown types are preserved. The general sensor's
`other_digital_loans` counts everything except `epub` and `audiobook`, including
`podcast` and `paper_book`, without assigning them to an unsupported quota.
For Biblo, `ebooks + audiobooks + other_digital_loans` equals the digital loan
count; the loans sensor also includes physical loans.
Old eReolen templates may only recognize `zip` for audio. A template such as
`types[book.type] + book.title` fails for an unknown type and can hide the
entire list. Add the new types to the icon map and use a fallback lookup,
for example:

```jinja
{% set types = {
  'epub': '📜',
  'zip': '🎧',
  'audiobook': '🎧',
  'podcast': '🎙️',
  'bog': '📖'
} %}
{{ types.get(book.type, '📖') ~ book.title }}
```

Keep any other existing entries in your icon map. Apply the fallback to
loan, overdue-loan and reservation cards that look up icons by type.

Once enabled, the integration follows the website's new provider and does
not merge old Pubhub loans into the new list. Old loans that remain only in
the old platform are not included. The old reader remains relevant during
the transition; do not assume all old loans have migrated.

## Verification before release

Synthetic tests cover provider selection, token forwarding, pagination,
outage retention, local-time normalization, reservation offers, optional metadata,
separate/shared quotas, and the existing digital-loans toggle.

An authenticated smoke test against Sønderborg completed on 2026-11-19:
all four existing account entries loaded after a full restart, Biblio quotas
were populated for every account, digital audiobook loans were returned,
physical loans remained available, and the Home Assistant system log had
no entries matching `bibliotek_dk`. No credentials or patron payloads are
stored in this repository.

The existing dashboard initially failed on a `podcast` icon lookup. Adding
the new types and a fallback restored the loan list, which the user confirmed.
Comparison with the app, nonempty reservation/offer cases, the digital-loans
toggle, and token expiry still need manual verification. With an account at a
library that has switched (such as Sønderborg):

1. Load the integration and compare its digital loans with Biblo and the
   library website (title, author, count and return date).
2. Compare waiting reservations and any available offer's deadline.
3. Compare format and shared quotas against the website.
4. Disable digital loans and check that physical data still updates.
5. Reload, enable digital loans, and check the next refresh and a refresh
   after the login token expires.

Use local credentials only. Do not attach CPR numbers, PINs, tokens or
unredacted patron responses to issues, tests or pull requests.

## Primary sources

* [DDF: migration from Pubhub to Biblio LMS](https://www.detdigitalefolkebibliotek.dk/nyheder/pause-i-reserveringer-af-e-bger-og-lydbger).
* [DDF adapter OpenAPI contract](https://github.com/danskernesdigitalebibliotek/dpl-web/blob/main/schemas/openapi/biblio-adapter.yaml).
* [DDF website adapter configuration](https://github.com/danskernesdigitalebibliotek/dpl-web/blob/main/cms/web/modules/custom/dpl_biblio/src/Hook/ReactAppsHooks.php).
* [DDF read client and quota mapping](https://github.com/danskernesdigitalebibliotek/dpl-web/tree/main/packages/service-layer/biblio/src).
* [Sønderborg loan page](https://biblioteket.sonderborg.dk/user/me/loans).
