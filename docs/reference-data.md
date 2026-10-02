# Official registry reference data

The tracker uses official free bulk datasets to improve organisation identity matching. These datasets are identity references, not evidence of wrongdoing.

## Companies House

Documentation: https://www.gov.uk/guidance/companies-house-data-products

Bulk downloads:
- Basic company data: https://download.companieshouse.gov.uk/en_output.html
- People with significant control (PSC): https://download.companieshouse.gov.uk/en_pscdata.html

The retained index stores public identifiers, registered names, aliases and selected non-sensitive metadata relevant to organisations already present in the tracker. PSC address fields and dates of birth are not copied into the tracker.

## Charity Commission for England and Wales

Full-register download:
https://register-of-charities.charitycommission.gov.uk/en/register/full-register-download

The reference index stores charity identifiers, registered names, other names and linked Companies House identifiers where available.

## Storage policy

Large national bulk archives are disposable inputs and are not committed to Git. The retained index is deliberately compact and records the official dataset URL and dataset date.

## Matching policy

- exact public identifier: may auto-resolve
- sufficiently specific exact normalized name: may resolve under documented matcher rules
- fuzzy name similarity: human review only

Original source entities are not destructively merged.
