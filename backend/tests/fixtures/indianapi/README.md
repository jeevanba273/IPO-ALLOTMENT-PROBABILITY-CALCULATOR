# IndianAPI fixtures

Response bodies used by the ingestion tests. The companies are made up. The
shapes follow:

* `/ipo/v2`, `/ipo`, `/usage`: the examples in https://dev.indianapi.in/openapi.json
  (ISO dates like `2024-10-28T00:00:00.000`, numeric prices, `"..."` placeholder items)
* `/ipo/{id}`: no published example; the field names are the ones observed in real
  responses (`data.basic`, `data.pricing.priceRange`, `data.issue`, `data.dates`,
  `data.timeline`, `data.reservation`, `data.subscription.daily[]` with DD-MM-YYYY
  dates and numbers as strings, `data.documents`, `data.registrar`, `data.performance`)

They deliberately include things that must never be stored: signed RHP/DRHP
links, registrar contact details, formatted strings and SEBI document links.

"Today" in the tests is Tuesday 03 Nov 2026, 10:00 IST.
