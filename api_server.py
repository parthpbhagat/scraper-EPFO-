from __future__ import annotations

import argparse
import csv
import datetime as dt
import decimal
import io
import json
import os
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlparse

import mysql.connector

from epfo_scraper import MYSQL_DEFAULTS
from export_company_csvs import CSV_HEADERS, DEFAULT_OVERRIDE_FILES, load_cin_overrides, payload_to_csv_rows
from export_company_excels import MONTH_YEAR, clean_filename_part, fetch_company_payload


RAW_COLUMNS = {"raw_result_html", "raw_html", "traceback_text"}
JSON_TEXT_COLUMNS = {"raw_row_json", "row_json"}
CSV_NO_PAYMENT_MESSAGE = "No EPFO payment rows found in the project database for this company."


@dataclass(frozen=True)
class FilterConfig:
    param: str
    column: str
    mode: str = "exact"


@dataclass(frozen=True)
class TableConfig:
    route: str
    table: str
    order_by: str
    filters: tuple[FilterConfig, ...] = ()
    search_columns: tuple[str, ...] = ()


TABLES: dict[str, TableConfig] = {
    "scrape-runs": TableConfig(
        route="scrape-runs",
        table="scrape_runs",
        order_by="id DESC",
        filters=(FilterConfig("status", "status"),),
        search_columns=("source_file", "status", "notes"),
    ),
    "company-queries": TableConfig(
        route="company-queries",
        table="company_queries",
        order_by="id DESC",
        filters=(
            FilterConfig("id", "id"),
            FilterConfig("run_id", "run_id"),
            FilterConfig("status", "status"),
            FilterConfig("company_name", "company_name", "like"),
            FilterConfig("matched_search_term", "matched_search_term", "like"),
        ),
        search_columns=("company_name", "matched_search_term", "status", "error_message"),
    ),
    "establishments": TableConfig(
        route="establishments",
        table="establishments",
        order_by="last_seen_at DESC",
        filters=(
            FilterConfig("est_id", "est_id"),
            FilterConfig("office_name", "office_name", "like"),
            FilterConfig("establishment_name", "establishment_name", "like"),
        ),
        search_columns=("est_id", "establishment_name", "address", "office_name"),
    ),
    "company-query-establishments": TableConfig(
        route="company-query-establishments",
        table="company_query_establishments",
        order_by="created_at DESC",
        filters=(FilterConfig("query_id", "query_id"), FilterConfig("est_id", "est_id")),
        search_columns=("est_id",),
    ),
    "establishment-section-data": TableConfig(
        route="establishment-section-data",
        table="establishment_section_data",
        order_by="id DESC",
        filters=(
            FilterConfig("id", "id"),
            FilterConfig("run_id", "run_id"),
            FilterConfig("query_id", "query_id"),
            FilterConfig("est_id", "est_id"),
            FilterConfig("section_name", "section_name"),
            FilterConfig("data_kind", "data_kind"),
            FilterConfig("field_name", "field_name", "like"),
        ),
        search_columns=("est_id", "section_name", "field_name", "field_value", "row_json"),
    ),
    "payment-details": TableConfig(
        route="payment-details",
        table="payment_details",
        order_by="id DESC",
        filters=(
            FilterConfig("id", "id"),
            FilterConfig("est_id", "est_id"),
            FilterConfig("trrn", "trrn"),
            FilterConfig("wage_month", "wage_month"),
            FilterConfig("ecr", "ecr"),
        ),
        search_columns=("est_id", "trrn", "date_of_credit", "amount", "wage_month", "ecr"),
    ),
    "establishment-raw-pages": TableConfig(
        route="establishment-raw-pages",
        table="establishment_raw_pages",
        order_by="id DESC",
        filters=(
            FilterConfig("id", "id"),
            FilterConfig("run_id", "run_id"),
            FilterConfig("est_id", "est_id"),
            FilterConfig("page_type", "page_type"),
        ),
        search_columns=("est_id", "page_type", "raw_html"),
    ),
    "scrape-errors": TableConfig(
        route="scrape-errors",
        table="scrape_errors",
        order_by="id DESC",
        filters=(
            FilterConfig("id", "id"),
            FilterConfig("run_id", "run_id"),
            FilterConfig("query_id", "query_id"),
            FilterConfig("est_id", "est_id"),
            FilterConfig("stage", "stage"),
        ),
        search_columns=("est_id", "stage", "error_message", "traceback_text"),
    ),
}


def env_mysql_config() -> dict[str, Any]:
    cfg: dict[str, Any] = {
        "host": os.getenv("MYSQL_HOST", MYSQL_DEFAULTS["MYSQL_HOST"]),
        "port": int(os.getenv("MYSQL_PORT", MYSQL_DEFAULTS["MYSQL_PORT"])),
        "user": os.getenv("MYSQL_USER", MYSQL_DEFAULTS["MYSQL_USER"]),
        "password": os.getenv("MYSQL_PASSWORD", MYSQL_DEFAULTS["MYSQL_PASSWORD"]),
        "database": os.getenv("MYSQL_DATABASE", MYSQL_DEFAULTS["MYSQL_DATABASE"]),
        "charset": "utf8mb4",
        "use_unicode": True,
    }
    ssl_ca = os.getenv("MYSQL_SSL_CA")
    if ssl_ca:
        cfg["ssl_ca"] = ssl_ca
        cfg["ssl_verify_cert"] = os.getenv("MYSQL_SSL_VERIFY", "true").lower() in {"1", "true", "yes"}
    return cfg


def db_connect() -> mysql.connector.MySQLConnection:
    return mysql.connector.connect(**env_mysql_config())


def parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "y", "on"}


def parse_int(value: str | None, default: int, minimum: int, maximum: int) -> int:
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return min(max(parsed, minimum), maximum)


def first_param(params: dict[str, list[str]], name: str) -> str | None:
    values = params.get(name)
    if not values:
        return None
    value = values[0].strip()
    return value if value else None


def json_default(value: Any) -> str | int | float | None:
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return str(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def parse_json_text(value: Any) -> Any:
    if not isinstance(value, str) or not value:
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def public_row(row: dict[str, Any], include_raw: bool) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in row.items():
        if key in JSON_TEXT_COLUMNS:
            result[key] = parse_json_text(value)
            continue
        if key in RAW_COLUMNS and not include_raw:
            text = value or ""
            result[f"{key}_length"] = len(text)
            result[f"{key}_preview"] = text[:300] if isinstance(text, str) else ""
            continue
        result[key] = value
    return result


def build_where(config: TableConfig, params: dict[str, list[str]]) -> tuple[str, list[Any], dict[str, str]]:
    clauses: list[str] = []
    values: list[Any] = []
    applied: dict[str, str] = {}

    for filter_config in config.filters:
        value = first_param(params, filter_config.param)
        if value is None:
            continue
        if filter_config.mode == "like":
            clauses.append(f"{filter_config.column} LIKE %s")
            values.append(f"%{value}%")
        else:
            clauses.append(f"{filter_config.column} = %s")
            values.append(value)
        applied[filter_config.param] = value

    q = first_param(params, "q")
    if q and config.search_columns:
        search_clause = " OR ".join(f"{column} LIKE %s" for column in config.search_columns)
        clauses.append(f"({search_clause})")
        values.extend([f"%{q}%"] * len(config.search_columns))
        applied["q"] = q

    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return where_sql, values, applied


def fetch_table(config: TableConfig, params: dict[str, list[str]]) -> dict[str, Any]:
    limit = parse_int(first_param(params, "limit"), default=100, minimum=1, maximum=1000)
    offset = parse_int(first_param(params, "offset"), default=0, minimum=0, maximum=1_000_000)
    include_raw = parse_bool(first_param(params, "include_raw"), default=False)
    where_sql, values, applied_filters = build_where(config, params)

    count_sql = f"SELECT COUNT(*) AS total FROM {config.table} {where_sql}"
    data_sql = (
        f"SELECT * FROM {config.table} {where_sql} "
        f"ORDER BY {config.order_by} LIMIT %s OFFSET %s"
    )

    conn = db_connect()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(count_sql, values)
        total = int(cursor.fetchone()["total"])
        cursor.execute(data_sql, [*values, limit, offset])
        rows = [public_row(row, include_raw) for row in cursor.fetchall()]
        cursor.close()
    finally:
        conn.close()

    return {
        "ok": True,
        "table": config.table,
        "endpoint": f"/api/{config.route}",
        "filters": applied_filters,
        "pagination": {
            "limit": limit,
            "offset": offset,
            "count": len(rows),
            "total": total,
            "next_offset": offset + limit if offset + limit < total else None,
        },
        "include_raw": include_raw,
        "data": rows,
    }


def fetch_schema(route: str) -> dict[str, Any]:
    config = TABLES[route]
    conn = db_connect()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(f"SHOW COLUMNS FROM {config.table}")
        columns = cursor.fetchall()
        cursor.close()
    finally:
        conn.close()
    return {"ok": True, "table": config.table, "endpoint": f"/api/{route}", "columns": columns}


def health_response() -> dict[str, Any]:
    conn = db_connect()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        cursor.fetchone()
        cursor.close()
    finally:
        conn.close()
    return {
        "ok": True,
        "database": env_mysql_config()["database"],
        "tables": len(TABLES),
        "endpoints": [f"/api/{route}" for route in TABLES],
    }


def tables_response() -> dict[str, Any]:
    return {
        "ok": True,
        "export_endpoints": [
            {
                "endpoint": "/api/company-csv",
                "method": "GET",
                "params": ["company_name", "run_id", "month_year"],
                "example": "/api/company-csv?company_name=Bird%20Delhi%20General%20Aviation%20Services%20Private%20Limited",
            }
        ],
        "tables": [
            {
                "table": config.table,
                "endpoint": f"/api/{config.route}",
                "schema_endpoint": f"/api/schema/{config.route}",
                "filters": [filter_config.param for filter_config in config.filters],
                "search_param": "q" if config.search_columns else None,
            }
            for config in TABLES.values()
        ],
    }


def resolve_company_name(cursor, requested_name: str) -> tuple[str | None, list[str]]:
    cursor.execute(
        """
        SELECT company_name
        FROM company_queries
        WHERE company_name = %s
        ORDER BY (status = 'completed') DESC, run_id DESC, id DESC
        LIMIT 1
        """,
        (requested_name,),
    )
    exact = cursor.fetchone()
    if exact:
        return str(exact["company_name"]), []

    cursor.execute(
        """
        SELECT company_name
        FROM company_queries
        WHERE company_name LIKE %s
        GROUP BY company_name
        ORDER BY MAX(status = 'completed') DESC, MAX(id) DESC
        LIMIT 20
        """,
        (f"%{requested_name}%",),
    )
    matches = [str(row["company_name"]) for row in cursor.fetchall()]
    if len(matches) == 1:
        return matches[0], []
    return None, matches


def csv_bytes_from_payload(
    payload: dict[str, Any],
    month_year: str,
    cin_overrides: dict[str, str],
) -> tuple[str, bytes, int, bool]:
    cin, rows = payload_to_csv_rows(payload, cin_overrides)
    has_payment_rows = not (rows and str(rows[0][0]).startswith(CSV_NO_PAYMENT_MESSAGE))

    text_buffer = io.StringIO(newline="")
    writer = csv.writer(text_buffer)
    writer.writerow([payload["company_name"].upper()])
    writer.writerow([])
    writer.writerow(CSV_HEADERS)
    writer.writerows(rows)

    company_part = clean_filename_part(payload["company_name"], 100)
    cin_part = clean_filename_part(cin, 30)
    filename = f"{company_part}_EPFO_{cin_part}_{month_year}.csv"
    return filename, text_buffer.getvalue().encode("utf-8-sig"), len(rows), has_payment_rows


def fetch_company_csv_response(params: dict[str, list[str]]) -> tuple[str, bytes, dict[str, Any]]:
    requested_name = first_param(params, "company_name") or first_param(params, "company") or first_param(params, "q")
    if not requested_name:
        raise ValueError("company_name query parameter is required.")

    run_id = parse_int(first_param(params, "run_id"), default=0, minimum=0, maximum=1_000_000_000)
    month_year = first_param(params, "month_year") or MONTH_YEAR
    cin_overrides = load_cin_overrides([Path(path) for path in DEFAULT_OVERRIDE_FILES])

    conn = db_connect()
    try:
        cursor = conn.cursor(dictionary=True)
        resolved_name, matches = resolve_company_name(cursor, requested_name)
        if not resolved_name:
            if matches:
                raise LookupError(json.dumps({"message": "Multiple companies matched. Use exact company_name.", "matches": matches}))
            raise LookupError(json.dumps({"message": "Company not found in database.", "matches": []}))

        payload = fetch_company_payload(cursor, run_id, resolved_name)
        if payload["query"] is None:
            raise LookupError(json.dumps({"message": "Company not found in database.", "matches": []}))

        filename, encoded, row_count, has_payment_rows = csv_bytes_from_payload(payload, month_year, cin_overrides)
        metadata = {
            "company_name": resolved_name,
            "filename": filename,
            "run_id": run_id or "latest completed per company",
            "rows": row_count,
            "has_payment_rows": has_payment_rows,
        }
        cursor.close()
    finally:
        conn.close()

    return filename, encoded, metadata


class ApiHandler(BaseHTTPRequestHandler):
    server_version = "EPFOJsonAPI/1.0"

    def log_message(self, format: str, *args: Any) -> None:
        print(f"{self.address_string()} - {format % args}")

    def send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload, ensure_ascii=False, default=json_default).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(encoded)

    def send_csv(self, filename: str, encoded: bytes, metadata: dict[str, Any]) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Content-Disposition", f"attachment; filename=\"{filename}\"; filename*=UTF-8''{quote(filename)}")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Expose-Headers", "Content-Disposition, X-EPFO-Rows, X-EPFO-Has-Payment-Rows")
        self.send_header("X-EPFO-Rows", str(metadata["rows"]))
        self.send_header("X-EPFO-Has-Payment-Rows", "true" if metadata["has_payment_rows"] else "false")
        self.end_headers()
        self.wfile.write(encoded)

    def do_OPTIONS(self) -> None:
        self.send_json(HTTPStatus.OK, {"ok": True})

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path_parts = [part for part in parsed.path.strip("/").split("/") if part]
        params = parse_qs(parsed.query, keep_blank_values=False)

        try:
            if parsed.path in {"/", "/api"}:
                self.send_json(HTTPStatus.OK, tables_response())
                return

            if path_parts == ["api", "health"]:
                self.send_json(HTTPStatus.OK, health_response())
                return

            if path_parts == ["api", "tables"]:
                self.send_json(HTTPStatus.OK, tables_response())
                return

            if path_parts == ["api", "company-csv"]:
                filename, encoded, metadata = fetch_company_csv_response(params)
                self.send_csv(filename, encoded, metadata)
                return

            if len(path_parts) == 3 and path_parts[:2] == ["api", "schema"]:
                route = path_parts[2]
                if route not in TABLES:
                    self.send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Unknown table route."})
                    return
                self.send_json(HTTPStatus.OK, fetch_schema(route))
                return

            if len(path_parts) == 2 and path_parts[0] == "api":
                route = path_parts[1]
                config = TABLES.get(route)
                if not config:
                    self.send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Unknown API endpoint."})
                    return
                self.send_json(HTTPStatus.OK, fetch_table(config, params))
                return

            self.send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Endpoint not found."})
        except mysql.connector.Error as exc:
            self.send_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"ok": False, "error": "Database error", "message": str(exc)},
            )
        except ValueError as exc:
            self.send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
        except LookupError as exc:
            try:
                detail = json.loads(str(exc))
            except json.JSONDecodeError:
                detail = {"message": str(exc), "matches": []}
            status = HTTPStatus.BAD_REQUEST if detail.get("matches") else HTTPStatus.NOT_FOUND
            self.send_json(HTTPStatus(status), {"ok": False, "error": detail["message"], "matches": detail["matches"]})
        except Exception as exc:  # noqa: BLE001 - API should return JSON for unexpected errors.
            self.send_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"ok": False, "error": "Server error", "message": str(exc)},
            )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="JSON API server for EPFO scraper MySQL tables.")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind.")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    server = ThreadingHTTPServer((args.host, args.port), ApiHandler)
    print(f"EPFO JSON API running at http://{args.host}:{args.port}")
    print("Open http://127.0.0.1:8000/api/tables to see all endpoints.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping API server.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
