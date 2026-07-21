from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

import mysql.connector

from export_company_excels import (
    DEFAULT_CIN_OVERRIDES,
    MONTH_YEAR,
    clean_filename_part,
    delay_text,
    display_date,
    due_date_for_wage_month,
    fetch_company_payload,
    meaningful_code,
    mysql_config,
    trust_status,
)


DEFAULT_OUTPUT_DIR = "outputs/tidb_company_epfo_csvs"
DEFAULT_OVERRIDE_FILES = (
    DEFAULT_CIN_OVERRIDES,
    "sample_10_cin_overrides.json",
    "all_cin_overrides.json",
)

CSV_HEADERS = [
    "ESTABLISHMENT CODE",
    "PRIVATE TRUST/NON PRIVATE TRUST",
    "STATUS",
    "OFFICE NAME",
    "WAGE MONTH",
    "DUE DATE",
    "FIRST DATE OF CREDIT",
    "DELAY",
    "AMOUNT",
    "NO. OF \n EMPLOYEES",
]


def read_company_names(path: Path) -> list[str]:
    names: list[str] = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        value = line.strip()
        if value and not value.startswith("#"):
            names.append(value)
    return names


def fetch_database_companies(cursor, completed_only: bool) -> list[str]:
    status_filter = "WHERE status = 'completed'" if completed_only else ""
    cursor.execute(
        f"""
        SELECT company_name
        FROM company_queries
        {status_filter}
        GROUP BY company_name
        ORDER BY MIN(query_order), MIN(id)
        """
    )
    return [str(row["company_name"]) for row in cursor.fetchall()]


def load_cin_overrides(paths: list[Path]) -> dict[str, str]:
    overrides: dict[str, str] = {}
    for path in paths:
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        overrides.update(
            {
                str(company).strip().upper(): str(cin).strip()
                for company, cin in data.items()
                if str(company).strip() and str(cin).strip()
            }
        )
    return overrides


def payload_to_csv_rows(payload: dict[str, Any], cin_overrides: dict[str, str]) -> tuple[str, list[list[Any]]]:
    company_name = payload["company_name"]
    cin = cin_overrides.get(company_name.upper(), "NA")
    rows: list[list[Any]] = []

    for item in payload["establishments"]:
        establishment = item["establishment"]
        fields = item["fields"]
        payments = item["payments"]
        cin = meaningful_code(fields.get("CIN Code")) or cin
        state_or_office = fields.get("State") or establishment.get("office_name") or "-"
        working_status = fields.get("Working Status") or "-"
        trust = trust_status(fields.get("Exemption Status"))

        for payment in payments:
            due_date = due_date_for_wage_month(payment["wage_month"])
            first_credit = payment["first_credit_date"]
            rows.append(
                [
                    payment["est_id"],
                    trust,
                    working_status,
                    state_or_office,
                    payment["wage_month"],
                    display_date(due_date),
                    display_date(first_credit),
                    delay_text(first_credit, due_date),
                    payment["amount"],
                    payment["no_of_employee"],
                ]
            )

    if not rows:
        rows.append(
            [
                "No EPFO payment rows found in the project database for this company.",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
            ]
        )

    return cin or "NA", rows


def save_company_csv(output_dir: Path, payload: dict[str, Any], month_year: str, cin: str, rows: list[list[Any]]) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    company_part = clean_filename_part(payload["company_name"], 100)
    cin_part = clean_filename_part(cin, 30)
    output_path = output_dir / f"{company_part}_EPFO_{cin_part}_{month_year}.csv"

    for old_path in output_dir.glob(f"{company_part}_EPFO_*_{month_year}.csv"):
        if old_path == output_path:
            continue
        old_path.unlink()

    with output_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow([payload["company_name"].upper()])
        writer.writerow([])
        writer.writerow(CSV_HEADERS)
        writer.writerows(rows)

    return output_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export TiDB/MySQL EPFO data into separate sample-style CSV files.")
    parser.add_argument("--company-file", default="", help="Optional company list. Blank exports all companies in DB.")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help="Folder for generated CSV files.")
    parser.add_argument("--month-year", default=MONTH_YEAR, help="Month_Year suffix for filenames.")
    parser.add_argument(
        "--run-id",
        type=int,
        default=0,
        help="Scrape run id. 0 uses latest completed query per company when available.",
    )
    parser.add_argument(
        "--completed-only",
        action="store_true",
        help="Export only companies with a completed company_queries row.",
    )
    parser.add_argument(
        "--cin-overrides",
        action="append",
        default=[],
        help="Optional JSON company-to-CIN override file. Can be passed multiple times.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    override_paths = [Path(path) for path in (*DEFAULT_OVERRIDE_FILES, *args.cin_overrides)]
    cin_overrides = load_cin_overrides(override_paths)
    output_dir = Path(args.output_dir)

    conn = mysql.connector.connect(**mysql_config())
    try:
        cursor = conn.cursor(dictionary=True)
        if args.company_file:
            companies = read_company_names(Path(args.company_file))
        else:
            companies = fetch_database_companies(cursor, args.completed_only)

        if not companies:
            print("No companies found for export.")
            return 2

        created: list[tuple[Path, int]] = []
        for company in companies:
            payload = fetch_company_payload(cursor, args.run_id, company)
            cin, rows = payload_to_csv_rows(payload, cin_overrides)
            created.append((save_company_csv(output_dir, payload, args.month_year, cin, rows), len(rows)))

        print(f"Database: {mysql_config()['host']}:{mysql_config()['port']}/{mysql_config()['database']}")
        print(f"Run ID used: {args.run_id or 'latest completed per company'}")
        print(f"Created {len(created)} CSV file(s):")
        for path, row_count in created:
            print(f"{path.resolve()} | rows: {row_count}")
        cursor.close()
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
