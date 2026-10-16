# POS Reconciliation Agent

A small, auditable Python tool that summarizes daily POS sales, checks
card settlement arithmetic, matches settlements to bank deposits, flags
unmatched records, and optionally emails an owner's report.

## Requirements

- Python 3.11+
- CSV exports from your POS, payment processor, and bank

## Setup

```bash
bash run.sh

Put the three CSV files in data/inbox/ using the headers shown in the
repository examples. Amounts should be decimal values without currency
symbols; dates should use YYYY-MM-DD.

Run
bash


pos-reconcile --date 2026-10-16
pos-reconcile --date 2026-10-16 --date-window 4 --tolerance 0.01
pos-reconcile --date 2026-10-16 --email
The report is printed to the terminal and saved under reports/.
The process exits with status 1 if anomalies were found, 0 if none
were found, and 2 if an input or configuration error occurred.

Email
Copy .env.example to .env, configure the SMTP settings, then export
those values into the process environment before running with --email.
Do not commit .env or real credentials.

Matching behavior
A settlement can match one unused bank deposit when the amount is within
the configured tolerance and the deposit date is from the settlement
date through the configured number of days afterward. A matching
settlement ID in the bank reference is preferred. Unmatched records are
flagged for review.

Important scope
This MVP expects CSV exports with the documented columns. Confirm that
your POS and processor define gross sales, refunds, fees, and net amounts
the same way before relying on the arithmetic checks. It does not move
money, initiate refunds, or replace review of exceptions. Test with
historical exports before using it for business decisions.




The next useful step is adapting the CSV column mappings to the exact exports from your POS, card processor, and bank; those formats vary by provider.