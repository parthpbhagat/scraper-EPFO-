"""
TiDB Migration Tool for EPFO Scraper
Copies all existing tables and data from local MySQL to TiDB,
and creates a .env configuration for all future scraper and API operations.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

import mysql.connector
from epfo_scraper import MYSQL_DEFAULTS, MySQLStore


TABLE_ORDER = [
    "scrape_runs",
    "company_queries",
    "establishments",
    "company_query_establishments",
    "establishment_section_data",
    "payment_details",
    "establishment_raw_pages",
    "scrape_errors",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migrate local MySQL EPFO scraper data to TiDB.")

    # Local MySQL source
    parser.add_argument("--local-host", default=MYSQL_DEFAULTS["MYSQL_HOST"], help="Local MySQL host.")
    parser.add_argument("--local-port", type=int, default=int(MYSQL_DEFAULTS["MYSQL_PORT"]), help="Local MySQL port.")
    parser.add_argument("--local-user", default=MYSQL_DEFAULTS["MYSQL_USER"], help="Local MySQL user.")
    parser.add_argument("--local-password", default=MYSQL_DEFAULTS["MYSQL_PASSWORD"], help="Local MySQL password.")
    parser.add_argument("--local-database", default=MYSQL_DEFAULTS["MYSQL_DATABASE"], help="Local MySQL database name.")

    # TiDB target
    parser.add_argument("--tidb-host", default=os.getenv("TIDB_HOST", ""), help="Target TiDB host.")
    parser.add_argument("--tidb-port", type=int, default=int(os.getenv("TIDB_PORT", "4000")), help="Target TiDB port (default: 4000).")
    parser.add_argument("--tidb-user", default=os.getenv("TIDB_USER", ""), help="Target TiDB user (e.g. user.root).")
    parser.add_argument("--tidb-password", default=os.getenv("TIDB_PASSWORD", ""), help="Target TiDB password.")
    parser.add_argument("--tidb-database", default=os.getenv("TIDB_DATABASE", "provident_fund"), help="Target TiDB database name.")
    parser.add_argument("--tidb-ssl-ca", default=os.getenv("TIDB_SSL_CA", ""), help="Path to CA certificate for SSL (if required).")

    parser.add_argument("--save-env", action="store_true", help="Save TiDB credentials to .env file so scraper uses TiDB by default.")
    parser.add_argument("--batch-size", type=int, default=500, help="Batch size for copying rows.")

    return parser.parse_args()


def connect_local(args: argparse.Namespace) -> mysql.connector.MySQLConnection:
    return mysql.connector.connect(
        host=args.local_host,
        port=args.local_port,
        user=args.local_user,
        password=args.local_password,
        database=args.local_database,
        charset="utf8mb4",
        use_unicode=True,
    )


def connect_tidb(args: argparse.Namespace) -> mysql.connector.MySQLConnection:
    kwargs: dict[str, Any] = {
        "host": args.tidb_host,
        "port": args.tidb_port,
        "user": args.tidb_user,
        "password": args.tidb_password,
        "charset": "utf8mb4",
        "use_unicode": True,
    }
    if args.tidb_ssl_ca:
        kwargs["ssl_ca"] = args.tidb_ssl_ca
        kwargs["ssl_verify_cert"] = True

    conn = mysql.connector.connect(**kwargs)
    cursor = conn.cursor()
    cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{args.tidb_database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
    cursor.execute(f"USE `{args.tidb_database}`")
    conn.commit()
    cursor.close()

    # Ensure schema
    os.environ["MYSQL_HOST"] = args.tidb_host
    os.environ["MYSQL_PORT"] = str(args.tidb_port)
    os.environ["MYSQL_USER"] = args.tidb_user
    os.environ["MYSQL_PASSWORD"] = args.tidb_password
    os.environ["MYSQL_DATABASE"] = args.tidb_database
    if args.tidb_ssl_ca:
        os.environ["MYSQL_SSL_CA"] = args.tidb_ssl_ca

    store = MySQLStore()
    store.close()

    return conn


def copy_table(source_conn: Any, target_conn: Any, table_name: str, batch_size: int = 500) -> int:
    src_cursor = source_conn.cursor(dictionary=True)
    src_cursor.execute(f"SELECT * FROM `{table_name}`")
    rows = src_cursor.fetchall()
    src_cursor.close()

    if not rows:
        return 0

    columns = list(rows[0].keys())
    col_names = ", ".join(f"`{c}`" for c in columns)
    placeholders = ", ".join(["%s"] * len(columns))

    sql = f"INSERT IGNORE INTO `{table_name}` ({col_names}) VALUES ({placeholders})"

    tgt_cursor = target_conn.cursor()
    copied = 0

    for i in range(0, len(rows), batch_size):
        batch = rows[i : i + batch_size]
        batch_values = [tuple(row[c] for c in columns) for row in batch]
        tgt_cursor.executemany(sql, batch_values)
        target_conn.commit()
        copied += len(batch)

    tgt_cursor.close()
    return copied


def main() -> int:
    args = parse_args()

    if not args.tidb_host or not args.tidb_user:
        print("Error: --tidb-host and --tidb-user are required to connect to TiDB.")
        print("\nExample command:")
        print("  python migrate_to_tidb.py --tidb-host gateway01.ap-southeast-1.prod.aws.tidbcloud.com --tidb-port 4000 --tidb-user 3xxxx.root --tidb-password 'your_password' --save-env")
        return 1

    print(f"Connecting to local MySQL ({args.local_host}:{args.local_port}/{args.local_database})...")
    try:
        source_conn = connect_local(args)
        print("Connected to local MySQL successfully.")
    except Exception as exc:
        print(f"Failed to connect to local MySQL: {exc}")
        return 1

    print(f"Connecting to target TiDB ({args.tidb_host}:{args.tidb_port}/{args.tidb_database})...")
    try:
        target_conn = connect_tidb(args)
        print("Connected to target TiDB successfully and schema initialized.")
    except Exception as exc:
        print(f"Failed to connect to TiDB: {exc}")
        source_conn.close()
        return 1

    print("\nStarting data migration...")
    summary: dict[str, int] = {}
    total_rows = 0

    for table in TABLE_ORDER:
        try:
            copied = copy_table(source_conn, target_conn, table, args.batch_size)
            summary[table] = copied
            total_rows += copied
            print(f"  - Table '{table}': {copied} rows copied.")
        except Exception as exc:
            print(f"  - Table '{table}': Error copying - {exc}")

    source_conn.close()
    target_conn.close()

    print("\n-------------------------------------------")
    print(f"Migration completed! Total rows migrated: {total_rows}")
    print("-------------------------------------------")

    if args.save_env:
        env_content = (
            f"# TiDB Database Configuration\n"
            f"MYSQL_HOST={args.tidb_host}\n"
            f"MYSQL_PORT={args.tidb_port}\n"
            f"MYSQL_USER={args.tidb_user}\n"
            f"MYSQL_PASSWORD={args.tidb_password}\n"
            f"MYSQL_DATABASE={args.tidb_database}\n"
        )
        if args.tidb_ssl_ca:
            env_content += f"MYSQL_SSL_CA={args.tidb_ssl_ca}\n"

        env_file = Path(__file__).parent / ".env"
        env_file.write_text(env_content, encoding="utf-8")
        print(f"\nSaved TiDB configuration to {env_file.resolve()}.")
        print("All future runs of epfo_scraper.py and api_server.py will automatically use TiDB!")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
