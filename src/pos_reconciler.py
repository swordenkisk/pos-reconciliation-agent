#!/usr/bin/env python3
"""Reconcile POS sales, card settlements, and bank deposits."""

from __future__ import annotations

import argparse
import csv
import os
import smtplib
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from email.message import EmailMessage
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INBOX = ROOT / "data" / "inbox"
REPORTS = ROOT / "reports"
ZERO = Decimal("0")


@dataclass
class Anomaly:
    category: str
    record: str
    details: str


def money(value: str, *, file_name: str, row_number: int) -> Decimal:
    try:
        return Decimal(value.strip()).quantize(Decimal("0.01"))
    except (InvalidOperation, AttributeError):
        raise ValueError(
            f"{file_name}, row {row_number}: invalid money value {value!r}"
        )


def read_csv(path: Path, required_columns: set[str]) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"Required input file not found: {path}")

    with path.open("r", newline="", encoding="utf-8-sig") as file:
        reader = csv.DictReader(file)
        headers = set(reader.fieldnames or [])
        missing = required_columns - headers
        if missing:
            raise ValueError(
                f"{path.name}: missing required columns: {', '.join(sorted(missing))}"
            )
        return [
            {key: (value or "").strip() for key, value in row.items()}
            for row in reader
        ]


def parse_date(value: str, *, file_name: str, row_number: int) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError(
            f"{file_name}, row {row_number}: expected YYYY-MM-DD, got {value!r}"
        )


def load_sales() -> list[dict]:
    fields = {
        "business_date", "location", "tender",
        "gross_sales", "refunds", "fees", "net_sales",
    }
    rows = read_csv(INBOX / "sales.csv", fields)
    result = []

    for number, row in enumerate(rows, start=2):
        result.append({
            **row,
            "row_number": number,
            "business_date": parse_date(
                row["business_date"], file_name="sales.csv", row_number=number
            ),
            "gross_sales": money(
                row["gross_sales"], file_name="sales.csv", row_number=number
            ),
            "refunds": money(
                row["refunds"], file_name="sales.csv", row_number=number
            ),
            "fees": money(row["fees"], file_name="sales.csv", row_number=number),
            "net_sales": money(
                row["net_sales"], file_name="sales.csv", row_number=number
            ),
        })
    return result


def load_settlements() -> list[dict]:
    fields = {
        "settlement_date", "processor", "settlement_id",
        "gross_amount", "refunds", "fees", "net_amount",
    }
    rows = read_csv(INBOX / "settlements.csv", fields)
    result = []

    for number, row in enumerate(rows, start=2):
        result.append({
            **row,
            "row_number": number,
            "settlement_date": parse_date(
                row["settlement_date"],
                file_name="settlements.csv",
                row_number=number,
            ),
            "gross_amount": money(
                row["gross_amount"],
                file_name="settlements.csv",
                row_number=number,
            ),
            "refunds": money(
                row["refunds"], file_name="settlements.csv", row_number=number
            ),
            "fees": money(
                row["fees"], file_name="settlements.csv", row_number=number
            ),
            "net_amount": money(
                row["net_amount"],
                file_name="settlements.csv",
                row_number=number,
            ),
        })
    return result


def load_deposits() -> list[dict]:
    fields = {"deposit_date", "account", "description", "amount", "reference"}
    rows = read_csv(INBOX / "deposits.csv", fields)
    result = []

    for number, row in enumerate(rows, start=2):
        result.append({
            **row,
            "row_number": number,
            "deposit_date": parse_date(
                row["deposit_date"], file_name="deposits.csv", row_number=number
            ),
            "amount": money(
                row["amount"], file_name="deposits.csv", row_number=number
            ),
        })
    return result


def reconcile(
    sales: list[dict],
    settlements: list[dict],
    deposits: list[dict],
    report_date: date,
    date_window: int,
    tolerance: Decimal,
) -> tuple[str, list[Anomaly]]:
    anomalies: list[Anomaly] = []
    sales_for_day = [row for row in sales if row["business_date"] == report_date]

    sales_by_tender: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for row in sales_for_day:
        expected_net = row["gross_sales"] - row["refunds"] - row["fees"]
        sales_by_tender[row["tender"]] += row["net_sales"]
        if abs(expected_net - row["net_sales"]) > tolerance:
            anomalies.append(Anomaly(
                "sales_math_mismatch",
                f"sales.csv row {row['row_number']}",
                f"Expected net {expected_net:.2f}; reported {row['net_sales']:.2f}.",
            ))

    settlement_ids: set[str] = set()
    for row in settlements:
        settlement_id = row["settlement_id"]
        if settlement_id in settlement_ids:
            anomalies.append(Anomaly(
                "duplicate_settlement_id",
                settlement_id or f"settlements.csv row {row['row_number']}",
                "Settlement ID appears more than once.",
            ))
        settlement_ids.add(settlement_id)

        expected_net = row["gross_amount"] - row["refunds"] - row["fees"]
        if abs(expected_net - row["net_amount"]) > tolerance:
            anomalies.append(Anomaly(
                "settlement_math_mismatch",
                settlement_id or f"settlements.csv row {row['row_number']}",
                f"Expected net {expected_net:.2f}; reported {row['net_amount']:.2f}.",
            ))

    used_deposits: set[int] = set()
    matches: list[tuple[dict, dict]] = []
    unmatched_settlements: list[dict] = []

    for settlement in sorted(settlements, key=lambda item: item["settlement_date"]):
        candidates = []
        for index, deposit in enumerate(deposits):
            if index in used_deposits:
                continue

            day_gap = (deposit["deposit_date"] - settlement["settlement_date"]).days
            if not 0 <= day_gap <= date_window:
                continue
            if abs(deposit["amount"] - settlement["net_amount"]) > tolerance:
                continue

            reference = deposit["reference"].lower()
            settlement_id = settlement["settlement_id"].lower()
            reference_match = bool(settlement_id and settlement_id in reference)
            candidates.append((not reference_match, day_gap, index, deposit))

        if candidates:
            _, _, index, deposit = min(candidates, key=lambda item: item[:2])
            used_deposits.add(index)
            matches.append((settlement, deposit))
        else:
            unmatched_settlements.append(settlement)
            anomalies.append(Anomaly(
                "unmatched_settlement",
                settlement["settlement_id"]
                or f"settlements.csv row {settlement['row_number']}",
                f"No matching bank deposit within {date_window} day(s).",
            ))

    unmatched_deposits = [
        (index, deposit)
        for index, deposit in enumerate(deposits)
        if index not in used_deposits
    ]
    for _, deposit in unmatched_deposits:
        anomalies.append(Anomaly(
            "unmatched_deposit",
            deposit["reference"] or f"deposits.csv row {deposit['row_number']}",
            f"No matching settlement found for deposit of {deposit['amount']:.2f}.",
        ))

    daily_settlements = [
        row for row in settlements if row["settlement_date"] == report_date
    ]
    daily_deposits = [row for row in deposits if row["deposit_date"] == report_date]

    lines = [
        f"Owner's Daily POS Report — {report_date.isoformat()}",
        "",
        "POS sales by tender:",
    ]
    if sales_by_tender:
        for tender, amount in sorted(sales_by_tender.items()):
            lines.append(f"  {tender}: {amount:.2f}")
        lines.append(f"  Total reported net sales: {sum(sales_by_tender.values(), ZERO):.2f}")
    else:
        lines.append("  No sales rows found for this date.")

    lines += [
        "",
        f"Card settlements dated today: {len(daily_settlements)} "
        f"({sum((row['net_amount'] for row in daily_settlements), ZERO):.2f} net)",
        f"Bank deposits dated today: {len(daily_deposits)} "
        f"({sum((row['amount'] for row in daily_deposits), ZERO):.2f})",
        f"Settlement-to-deposit matches: {len(matches)}",
        f"Unmatched settlements in supplied files: {len(unmatched_settlements)}",
        f"Unmatched deposits in supplied files: {len(unmatched_deposits)}",
        f"Anomalies flagged: {len(anomalies)}",
    ]

    if anomalies:
        lines += ["", "Anomaly details:"]
        for anomaly in anomalies:
            lines.append(
                f"  [{anomaly.category}] {anomaly.record}: {anomaly.details}"
            )

    return "\n".join(lines) + "\n", anomalies


def send_email(subject: str, body: str) -> None:
    required = [
        "SMTP_HOST", "SMTP_PORT", "SMTP_USERNAME", "SMTP_PASSWORD",
        "REPORT_FROM", "REPORT_TO",
    ]
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise ValueError(f"Missing email settings: {', '.join(missing)}")

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = os.environ["REPORT_FROM"]
    message["To"] = os.environ["REPORT_TO"]
    message.set_content(body)

    with smtplib.SMTP(os.environ["SMTP_HOST"], int(os.environ["SMTP_PORT"])) as smtp:
        smtp.starttls()
        smtp.login(os.environ["SMTP_USERNAME"], os.environ["SMTP_PASSWORD"])
        smtp.send_message(message)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--date",
        default=date.today().isoformat(),
        help="Business/report date in YYYY-MM-DD format (default: today).",
    )
    parser.add_argument("--email", action="store_true", help="Email the report.")
    parser.add_argument("--date-window", type=int, default=4)
    parser.add_argument("--tolerance", default="0.01")
    args = parser.parse_args()

    try:
        report_date = date.fromisoformat(args.date)
        tolerance = Decimal(args.tolerance).quantize(Decimal("0.01"))
        if args.date_window < 0 or tolerance < ZERO:
            raise ValueError("Date window and tolerance must be non-negative.")

        sales = load_sales()
        settlements = load_settlements()
        deposits = load_deposits()
        report, anomalies = reconcile(
            sales, settlements, deposits, report_date,
            args.date_window, tolerance,
        )

        REPORTS.mkdir(parents=True, exist_ok=True)
        report_path = REPORTS / f"{report_date.isoformat()}-owner-report.txt"
        report_path.write_text(report, encoding="utf-8")
        print(report, end="")
        print(f"\nSaved report: {report_path}")

        if args.email:
            send_email(f"POS Owner Report — {report_date.isoformat()}", report)
            print("Email sent.")

        return 1 if anomalies else 0

    except (ValueError, FileNotFoundError, OSError, smtplib.SMTPException) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
