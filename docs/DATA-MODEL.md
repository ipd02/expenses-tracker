# Data model

Findings from a local spike against Enable Banking with a personal BBVA account. Facts only; no real data is stored in this repository.

## What BBVA returns

| Field | Notes |
|---|---|
| Amount, currency, direction | always present (`DBIT` / `CRDT`) |
| Status | booked only (`BOOK`); pending transactions are not returned |
| `entry_reference` | present on every row and unique, used as the transaction ID (`transaction_id` is empty) |
| `creditor.name` / `debtor.name` | counterparty names; some rows carry both |
| `remittance_information` | always a list with one string |
| `booking_date`, `value_date` | date only, no time of day |
| `bank_transaction_code` | only on some rows, not reliable |
| History | about one year on the first fetch |

No merchant category code and no balance after the transaction. A card payment appears only after the bank books it.

Trade Republic was evaluated and dropped: it returns amount and date but no counterparty text and no transaction ID.

## Normalized expense

| Field | Notes |
|---|---|
| `account_id` | provider + account uid |
| `source_key` | `account_id + entry_reference` (dedupe key) |
| `booking_date` | ISO date |
| `amount` | decimal string, always positive |
| `currency` | ISO 4217 |
| `direction` | `debit` or `credit` |
| `counterparty` | see "Counterparty rule" below |
| `payment_type` | first segment of the remittance text (the bank's own description of the payment kind) |
| `description` | remittance text |
| `category` | assigned in the app; null until set |
| `raw` | original provider payload, kept so parsing can be redone |
| `source` | `enablebanking` or `receipt` |

Parsing and categorization of `counterparty` and `description` are deferred.

## Counterparty rule (confirmed on real rows)

- **Card payments**: `creditor.name` is clean and is the merchant. The remittance text repeats it, padded with spaces and followed by the city and country code.
- **Direct debits and similar** (taxes, social security, utilities): `creditor` is null. The counterparty has to come from the remittance text.
- **Credits**: `debtor.name` when present.
- **Debits never carry `debtor.name`**; the `debtor_account.iban` on them is the account holder's own IBAN.

`remittance_information` is a one-element list holding a string of the form `<payment type> // <subtype> // <details>`, split on ` // `:
- Segment 1 describes the payment kind (for example a card payment at a type of merchant, such as fuel stations, or a direct debit of a specific kind). It is a free category signal.
- Segment 3 holds the details: merchant name, city and country for card payments; a reference number and a concept for direct debits.

So: `counterparty = creditor.name` for debits that have one, else `debtor.name` for credits, else the details segment of the remittance text (trimmed and whitespace-collapsed). `bank_transaction_code` is null on these rows.

## Operational facts to design around

- Refreshes without the user present are limited (typically about four per day per account); the scheduler must stay under that.
- Bank consent expires and must be renewed by the user. The app tracks the expiry date and alerts before it.
- The application's private key and the bank session IDs are the sensitive secrets. They live in SSM Parameter Store (SecureString) and are never logged.
