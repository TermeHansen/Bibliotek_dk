

# Bibliotek (library)
![image](https://user-images.githubusercontent.com/54498188/220741705-0e2aec42-e582-4161-ad13-d66e537c5272.png)
## Home Assistant custom integration for all public libraries in Denmark
## Installation
1. Add this repository to your HACS as a manual integration. when the integration is finished and stable, I will add it to HACS' default.
2. Download it in HACS
3. Restart your Home Assistant
## Configuration
All the configuration is done in GUI.

![image](https://user-images.githubusercontent.com/54498188/221042602-7aa01c03-5299-492a-bd38-0fc914b11c97.png)

1. Just add the integration on your integrationspage.
2. Fill the form with the needed and optional information (**required is bold**, optional is not):
- Name, added to the name of the integration, fx. "Faaborg-Midtfyn Bibliotekerne (Jacob Lindvig Henriksen)"
- **Municipality**, Home Assistant will try to determine this from the coordinates of your home zone
- **CPR - number / loannumber**
- **Pincode**
- Show eReolen (e-books and audiobooks), boolean (default true)
- Show loans, boolean (default true)
- Show debts (fees), boolean (default true)
- Show reservations, boolean (default true)
- Update interval, minutes (default 60)

The show/hide toggles can be changed later from the integration's **Configure**
(options) dialog. Unticking one stops those sensors from being created.

## Usage
With this custom integration for [Home Assistant](https://www.home-assistant.io/) you will probably never be late again on your returns.
The integration creates these sensors:

- [General Library Sensor](#general-library-sensor)
- [Loans sensor](#loans-sensor)
- [Loans Overdue sensor](#loans-overdue-sensor)
- [Debts sensor](#debts-sensor)
- [Reservations sensor](#reservations-sensor)
- [Reservations Ready sensor](#reservations-ready-sensor)

All sensors support the `show_*` options above; disabled groups are not created.
All sensors for an account are grouped under a single device in Home Assistant.

### Cards
`loans_card.yaml` is a drop-in card: one row per loan, across every configured
account, with the cover, title, author/type and "due in ...". It discovers the
Bibliotek sensors itself (from their attributes), so there is nothing to edit and
it keeps working when entities are renamed. Paste it into a dashboard (Add card →
Manual) or use `dashboard.yaml` as a starting point.

`loans_card_decluttering.yaml` is the same card as a
[decluttering-card](https://github.com/custom-cards/decluttering-card) template,
so you can pass a specific `entity` and `title`. (Home Assistant has no card
blueprints; decluttering-card is the community equivalent.)

Both need the HACS frontend cards `auto-entities` and `template-entity-row`; the
cover popup on tap additionally needs `browser_mod` (remove the `tap_action`
block if you do not use it).

### Renewing loans
Physical loans can be renewed from an automation or the Developer Tools with the
`bibliotek_dk.renew_loans` service. It takes the `config_entry_id` and one or
more `loan_ids` (the `renew_id` attribute on each item of the loans sensor) and
returns the per-loan renewal status:

```yaml
service: bibliotek_dk.renew_loans
data:
  config_entry_id: 0f1e2d3c4b5a69788796a5b4c3d2e1f0
  loan_ids:
    - "1234567"
```

### General Library Sensor
A general sensor with "days to return" in state (based on the next loan) and:
  - Loans
  - Loans overdue
  - Reservations
  - Reservations ready for pickup
  - Debts
  - Your profile information and settings from the library
  - *eBooks*
  - *eBooks quota*
  - *Audiobooks*
  - *Audiobooks quota*
### Loans sensor
- A "Loans" sensor with number of loans as state and all available details on the materials (if supplied by the library):
  - Title
  - Creators
  - Type
  - Date of loan
  - Date of return
  - Whether the material is renewable
  - Link to the info on the website (bibliotek.dk work page for physical items,
    a bibliotek.dk ISBN search for eReolen items)
  - URL to the bookcover
### Loans Overdue sensor
Like the [Loans sensor](#loans-sensor), but only for loans past their return
date.
### Debts sensor
- A "Debts" sensor with the total outstanding amount as state, and per fee:
  - Title
  - Type
  - Date of the fee
  - Fee type
  - Fee amount
  - Link to the info on the website
  - URL to the bookcover
### Reservations sensor
Almost identical to the [Loans sensor](#loans-sensor) with these differences:
- Queue number
- Date of reservation
- Date of expiration
- Pick-up location
- ~~Date of loan~~
- ~~Date of return~~
- ~~Whether the material is renewable~~
### Reservations Ready sensor
Again almost identical to the prior [Reservations sensor](#reservations-sensor), but with these differences:
- Reservation number
- Date of available for pickup
- Date of last chance for pickup
- Pick-up location
- ~~Queue number~~

## Credits

This integration started out as a fork of
[J-Lindvig/Bibliotek_dk](https://github.com/J-Lindvig/Bibliotek_dk) by
Jacob Lindvig Henriksen. Thank you for the original work — this project would
not exist without it.
