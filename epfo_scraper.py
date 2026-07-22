from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import html
import json
import os
import re
import sys
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import mysql.connector
import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


BASE_URL = "https://unifiedportal-emp.epfindia.gov.in"
HOME_URL = f"{BASE_URL}/publicPortal/no-auth/misReport/home/loadEstSearchHome"

def load_env_file() -> None:
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ[key.strip()] = val.strip().strip("'\"")

load_env_file()

MYSQL_DEFAULTS = {
    "MYSQL_HOST": "127.0.0.1",
    "MYSQL_PORT": "3306",
    "MYSQL_USER": "root",
    "MYSQL_PASSWORD": "",
    "MYSQL_DATABASE": "provident_fund",
}

SECTION_NAMES = [
    "validity_status_online_coverage",
    "establishment_status_epfo_master",
    "form_5a_establishment_details",
    "factory_details",
    "owner_details",
    "units_subcode_same_jurisdiction",
    "other_code_numbers_same_establishment",
    "branches_without_code",
    "establishments_same_pan",
    "additional_information",
]

EST_ID_RE = re.compile(r"\b[A-Z]{5}\d{7,10}[A-Z0-9]?\b")


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def slugify(value: str, max_len: int = 60) -> str:
    value = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_").lower()
    return (value[:max_len] or "company").strip("_")


def mysql_name(value: str) -> str:
    return "`" + value.replace("`", "``") + "`"


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def first_regex(pattern: str, value: str) -> str | None:
    match = re.search(pattern, value, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return None
    return html.unescape(match.group(1))


def quoted_args(value: str) -> list[str]:
    args: list[str] = []
    for single, double in re.findall(r"'((?:\\'|[^'])*)'|\"((?:\\\"|[^\"])*)\"", value or ""):
        item = single if single else double
        args.append(html.unescape(item))
    return args


def extract_urls_from_js(value: str) -> list[str]:
    urls: list[str] = []
    for arg in quoted_args(value):
        if arg.startswith(("http://", "https://", "/publicPortal/")):
            urls.append(arg)
    return urls


def normalize_est_id(value: str | None) -> str | None:
    text = clean_text(value).upper()
    if not text:
        return None
    compact = re.sub(r"[^A-Z0-9]", "", text)
    if EST_ID_RE.fullmatch(compact):
        return compact
    match = EST_ID_RE.search(text)
    if match:
        return match.group(0)
    match = EST_ID_RE.search(compact)
    if match:
        return match.group(0)
    return None


def first_index_containing(headers: list[str], needle: str, default: int) -> int:
    for index, header in enumerate(headers):
        if needle in header.lower():
            return index
    return default


def normalize_search_term(value: str) -> str:
    value = re.sub(r"\bPVT\.?\b", "PRIVATE", value, flags=re.IGNORECASE)
    value = re.sub(r"\bLTD\.?\b", "LIMITED", value, flags=re.IGNORECASE)
    value = re.sub(r"[^A-Za-z0-9&]+", " ", value)
    return clean_text(value)


def company_search_variants(company_name: str, max_variants: int = 4) -> list[str]:
    normalized = normalize_search_term(company_name)
    suffix_patterns = [
        r"\bPRIVATE\s+LIMITED\b\.?$",
        r"\bLIMITED\b\.?$",
        r"\bLLP\b\.?$",
        r"\bINC\b\.?$",
    ]

    candidates = [company_name.strip(), normalized]
    without_suffix = normalized
    for pattern in suffix_patterns:
        without_suffix = re.sub(pattern, "", without_suffix, flags=re.IGNORECASE).strip()
    if without_suffix and without_suffix != normalized:
        candidates.append(without_suffix)

    words = without_suffix.split()
    if len(words) >= 3:
        first_three = " ".join(words[:3])
        if len(first_three) >= 12:
            candidates.append(first_three)

    variants: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        candidate = normalize_search_term(candidate)
        key = candidate.upper()
        if candidate and key not in seen:
            variants.append(candidate)
            seen.add(key)
        if len(variants) >= max(1, max_variants):
            break
    return variants


@dataclass
class SearchEndpoints:
    search_url: str
    captcha_url: str


@dataclass
class SearchResult:
    est_id: str
    establishment_name: str
    address: str
    office_name: str
    action_text: str
    detail_urls: list[str]
    raw_row: dict[str, str]


@dataclass
class SectionRecord:
    data_kind: str
    table_no: int
    row_no: int
    field_name: str | None = None
    field_value: str | None = None
    row_json: dict[str, str] | None = None


@dataclass
class DetailFetchResult:
    est_id: str
    raw_pages: list[tuple[str, str]]
    sections: list[tuple[str, list[SectionRecord]]]
    payments: list[dict[str, str]]
    errors: list[tuple[str, str, str]]


def parse_home_endpoints(page_html: str) -> SearchEndpoints:
    soup = BeautifulSoup(page_html, "html.parser")

    search_url = None
    search_button = soup.find(id="searchEmployer")
    if search_button:
        search_url = first_regex(r"fnSearchEstb\('([^']+)'", search_button.get("onclick", ""))
    if not search_url:
        search_url = first_regex(r"fnSearchEstb\('([^']+)'", page_html)

    captcha_img = soup.find(id="capImg")
    captcha_url = captcha_img.get("src") if captcha_img else None
    if not captcha_url:
        captcha_url = first_regex(r'<img[^>]+id=["\']capImg["\'][^>]+src=["\']([^"\']+)', page_html)

    if not search_url or not captcha_url:
        raise RuntimeError("Could not find EPFO search/captcha URLs on the home page.")

    return SearchEndpoints(search_url=search_url, captcha_url=captcha_url)


def parse_search_results(result_html: str) -> list[SearchResult]:
    soup = BeautifulSoup(result_html, "html.parser")
    results: list[SearchResult] = []

    for table in soup.find_all("table"):
        header_row = table.find("tr")
        headers = [clean_text(th.get_text(" ")) for th in header_row.find_all("th", recursive=False)] if header_row else []
        normalized = [h.lower() for h in headers]
        if not any("establishment id" in h for h in normalized):
            continue

        est_id_index = first_index_containing(headers, "establishment id", 0)
        name_index = first_index_containing(headers, "establishment name", 1)
        address_index = first_index_containing(headers, "address", 2)
        office_index = first_index_containing(headers, "office name", 3)
        action_index = first_index_containing(headers, "action", len(headers) - 1)

        for tr in table.find_all("tr"):
            cells = tr.find_all(["td", "th"], recursive=False)
            if not cells or cells[0].name == "th":
                continue
            values = [clean_text(cell.get_text(" ")) for cell in cells]
            if len(values) < 4:
                continue

            raw_row = {
                headers[index] if index < len(headers) else f"column_{index + 1}": values[index]
                for index in range(len(values))
            }
            action_cell = cells[action_index] if 0 <= action_index < len(cells) else cells[-1]
            action_text = clean_text(action_cell.get_text(" "))
            detail_urls: list[str] = []
            est_id_from_action: str | None = None
            for tag in action_cell.find_all(attrs={"onclick": True}):
                onclick = tag.get("onclick", "")
                if "fnViewDetails" not in onclick:
                    continue
                est_id_from_action = normalize_est_id(tag.get("name")) or est_id_from_action
                args = quoted_args(onclick)
                if args:
                    if args[0].startswith(("/", "http")):
                        detail_urls = [arg for arg in args if arg.startswith(("/", "http"))]
                    else:
                        est_id_from_action = normalize_est_id(args[0]) or est_id_from_action
                        detail_urls = [arg for arg in args[1:] if arg.startswith(("/", "http"))]
                    break

            est_id = (
                est_id_from_action
                or normalize_est_id(values[est_id_index] if est_id_index < len(values) else "")
                or normalize_est_id(" ".join(values))
            )
            if not est_id:
                continue

            results.append(
                SearchResult(
                    est_id=est_id,
                    establishment_name=values[name_index] if name_index < len(values) else "",
                    address=values[address_index] if address_index < len(values) else "",
                    office_name=values[office_index] if office_index < len(values) else "",
                    action_text=action_text,
                    detail_urls=detail_urls,
                    raw_row=raw_row,
                )
            )

    return results


def captcha_error_message(result_html: str) -> str | None:
    soup_text = clean_text(BeautifulSoup(result_html, "html.parser").get_text(" "))
    combined = clean_text(f"{soup_text} {result_html}")
    lowered = combined.lower()
    if "captcha" not in lowered:
        return None

    captcha_error_phrases = (
        "invalid captcha",
        "captcha invalid",
        "incorrect captcha",
        "wrong captcha",
        "bad captcha",
        "captcha mismatch",
        "captcha not match",
        "captcha not matched",
        "not match captcha",
        "captcha does not match",
        "captcha doesn't match",
        "valid captcha",
        "correct captcha",
        "captcha is not valid",
        "captcha validation failed",
        "please enter captcha",
    )
    if not any(phrase in lowered for phrase in captcha_error_phrases):
        return None

    alert_match = re.search(
        r"alert\s*\(\s*['\"]([^'\"]*captcha[^'\"]*)['\"]\s*\)",
        result_html,
        flags=re.IGNORECASE,
    )
    if alert_match:
        return clean_text(alert_match.group(1))

    sentences = re.split(r"(?<=[.!?])\s+|\s{2,}", soup_text)
    for sentence in sentences:
        if "captcha" in sentence.lower():
            return clean_text(sentence)
    return "Invalid CAPTCHA."


def page_mentions_invalid_captcha(result_html: str) -> bool:
    return captcha_error_message(result_html) is not None


def parse_detail_section(section_html: str) -> list[SectionRecord]:
    soup = BeautifulSoup(section_html, "html.parser")
    records: list[SectionRecord] = []

    for table_no, table in enumerate(soup.find_all("table"), start=1):
        header_cells = table.find_all("th")
        headers = [clean_text(th.get_text(" ")) for th in header_cells]
        rows = table.find_all("tr")

        if headers:
            for row_no, tr in enumerate(rows, start=1):
                cells = tr.find_all(["td", "th"], recursive=False)
                if not cells or all(cell.name == "th" for cell in cells):
                    continue
                values = [clean_text(cell.get_text(" ")) for cell in cells]
                row = {
                    headers[index] if index < len(headers) and headers[index] else f"column_{index + 1}": values[index]
                    for index in range(len(values))
                }
                records.append(SectionRecord("row", table_no, row_no, row_json=row))
            continue

        for row_no, tr in enumerate(rows, start=1):
            cells = tr.find_all(["td", "th"], recursive=False)
            values = [clean_text(cell.get_text(" ")) for cell in cells]
            values = [value for value in values if value]
            if not values:
                continue

            if len(values) >= 3 and re.match(r"^[A-Za-z0-9]+\.?$", values[0]):
                records.append(
                    SectionRecord(
                        "field",
                        table_no,
                        row_no,
                        field_name=values[1],
                        field_value=values[2],
                        row_json={"serial": values[0], "label": values[1], "value": values[2]},
                    )
                )
            elif len(values) == 2:
                records.append(
                    SectionRecord(
                        "field",
                        table_no,
                        row_no,
                        field_name=values[0],
                        field_value=values[1],
                        row_json={"label": values[0], "value": values[1]},
                    )
                )
            else:
                records.append(
                    SectionRecord(
                        "row",
                        table_no,
                        row_no,
                        row_json={f"column_{index + 1}": value for index, value in enumerate(values)},
                    )
                )

    return records


def parse_payment_url(validity_html: str) -> str | None:
    soup = BeautifulSoup(validity_html, "html.parser")

    for anchor in soup.find_all("a"):
        anchor_text = clean_text(anchor.get_text(" ")).lower()
        attrs = " ".join(str(anchor.get(name, "")) for name in ("href", "onclick")).lower()
        if "payment" not in anchor_text and "payment" not in attrs:
            continue

        href = anchor.get("href", "")
        if href and href != "#" and not href.lower().startswith("javascript:"):
            return html.unescape(href)

        urls = extract_urls_from_js(anchor.get("onclick", ""))
        if urls:
            return urls[0]

    for tag in soup.find_all(attrs={"onclick": True}):
        onclick = tag.get("onclick", "")
        if "payment" in onclick.lower():
            urls = extract_urls_from_js(onclick)
            if urls:
                return urls[0]

    return None


def normalize_payment_header(header: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", header.lower()).strip("_")
    aliases = {
        "trrn": "trrn",
        "date_of_credit": "date_of_credit",
        "credit_date": "date_of_credit",
        "date_of_payment": "date_of_credit",
        "payment_date": "date_of_credit",
        "amount": "amount",
        "wage_month": "wage_month",
        "wage_month_year": "wage_month",
        "month_of_wage": "wage_month",
        "wage_period": "wage_month",
        "month": "wage_month",
        "no_of_employee": "no_of_employee",
        "no_of_employees": "no_of_employee",
        "employees": "no_of_employee",
        "emp_count": "no_of_employee",
        "number_of_employees": "no_of_employee",
        "ecr": "ecr",
    }
    return aliases.get(normalized, normalized or "column")


def parse_payment_rows(payment_html: str) -> list[dict[str, str]]:
    soup = BeautifulSoup(payment_html, "html.parser")
    payment_rows: list[dict[str, str]] = []

    for table in soup.find_all("table"):
        headers = [clean_text(th.get_text(" ")) for th in table.find_all("th")]
        normalized_headers = [normalize_payment_header(header) for header in headers]
        if "trrn" not in normalized_headers or "wage_month" not in normalized_headers:
            continue

        for tr in table.find_all("tr"):
            cells = tr.find_all(["td", "th"], recursive=False)
            if not cells or all(cell.name == "th" for cell in cells):
                continue
            values = [clean_text(cell.get_text(" ")) for cell in cells]
            row = {
                normalized_headers[index] if index < len(normalized_headers) else f"column_{index + 1}": values[index]
                for index in range(len(values))
            }
            if row.get("trrn"):
                payment_rows.append(row)

    return payment_rows


class EpfoClient:
    def __init__(self, timeout: int = 35, delay: float = 0.25) -> None:
        self.timeout = timeout
        self.delay = delay
        self.session = requests.Session()
        retry = Retry(
            total=3,
            read=3,
            connect=3,
            backoff_factor=0.7,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset(["GET", "POST"]),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=20, pool_maxsize=20)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Origin": BASE_URL,
                "Referer": HOME_URL,
            }
        )
        self.endpoints: SearchEndpoints | None = None

    def clone(self) -> "EpfoClient":
        other = EpfoClient(timeout=self.timeout, delay=self.delay)
        other.session.cookies.update(self.session.cookies)
        other.endpoints = self.endpoints
        return other

    def absolute_url(self, url: str) -> str:
        return urljoin(BASE_URL, html.unescape(url))

    def request_get(self, url: str, **kwargs: Any) -> requests.Response:
        time.sleep(self.delay)
        timeout = kwargs.pop("timeout", (10, self.timeout))
        response = self.session.get(self.absolute_url(url), timeout=timeout, **kwargs)
        response.raise_for_status()
        return response

    def request_post_json(self, url: str, payload: dict[str, Any]) -> requests.Response:
        time.sleep(self.delay)
        response = self.session.post(
            self.absolute_url(url),
            data=json.dumps(payload),
            headers={"Content-Type": "application/json; charset=utf-8", "Accept": "*/*"},
            timeout=(10, self.timeout),
        )
        response.raise_for_status()
        return response

    def load_home(self) -> str:
        response = self.request_get(HOME_URL)
        self.endpoints = parse_home_endpoints(response.text)
        return response.text

    def fetch_captcha_bytes(self) -> bytes:
        if not self.endpoints:
            self.load_home()
        assert self.endpoints is not None
        response = self.request_get(self.endpoints.captcha_url, headers={"Accept": "image/*,*/*;q=0.8"})
        return response.content

    def save_captcha(self, output_dir: Path, company_name: str, attempt: int) -> Path:
        if not self.endpoints:
            self.load_home()
        assert self.endpoints is not None

        output_dir.mkdir(parents=True, exist_ok=True)
        response = self.request_get(self.endpoints.captcha_url, headers={"Accept": "image/*,*/*;q=0.8"})
        content_type = response.headers.get("Content-Type", "").lower()
        extension = ".png" if "png" in content_type else ".jpg"
        filename = f"{dt.datetime.now():%Y%m%d_%H%M%S}_{slugify(company_name)}_attempt{attempt}{extension}"
        path = output_dir / filename
        path.write_bytes(response.content)
        return path

    def search_establishment(self, company_name: str, captcha: str, est_code: str = "") -> str:
        if not self.endpoints:
            self.load_home()
        assert self.endpoints is not None

        payload = {"EstName": company_name, "EstCode": est_code, "captcha": captcha}
        return self.request_post_json(self.endpoints.search_url, payload).text

    def fetch_detail_section(self, url: str, est_id: str) -> str:
        return self.request_post_json(url, {"EstId": est_id}).text

    def fetch_payment_details(self, url: str, est_id: str) -> str:
        try:
            response = self.request_post_json(url, {"EstId": est_id})
            if "trrn" in response.text.lower() or "payment" in response.text.lower():
                return response.text
        except requests.RequestException:
            pass
        return self.request_get(url).text


class MySQLStore:
    def __init__(self) -> None:
        self.host = os.getenv("MYSQL_HOST", MYSQL_DEFAULTS["MYSQL_HOST"])
        self.port = int(os.getenv("MYSQL_PORT", MYSQL_DEFAULTS["MYSQL_PORT"]))
        self.user = os.getenv("MYSQL_USER", MYSQL_DEFAULTS["MYSQL_USER"])
        self.password = os.getenv("MYSQL_PASSWORD", MYSQL_DEFAULTS["MYSQL_PASSWORD"])
        self.database = os.getenv("MYSQL_DATABASE", MYSQL_DEFAULTS["MYSQL_DATABASE"])
        self.ssl_ca = os.getenv("MYSQL_SSL_CA")
        self.ssl_verify = os.getenv("MYSQL_SSL_VERIFY", "true").lower() in {"1", "true", "yes"}

        kwargs: dict[str, Any] = {
            "host": self.host,
            "port": self.port,
            "user": self.user,
            "password": self.password,
            "charset": "utf8mb4",
            "use_unicode": True,
        }
        if self.ssl_ca:
            kwargs["ssl_ca"] = self.ssl_ca
            kwargs["ssl_verify_cert"] = self.ssl_verify

        self.conn = mysql.connector.connect(**kwargs)
        self.conn.autocommit = False
        self.ensure_database()
        self.ensure_schema()

    def close(self) -> None:
        self.conn.close()

    def ensure_database(self) -> None:
        cursor = self.conn.cursor()
        cursor.execute(
            f"CREATE DATABASE IF NOT EXISTS {mysql_name(self.database)} "
            "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
        )
        cursor.execute(f"USE {mysql_name(self.database)}")
        self.conn.commit()
        cursor.close()

    def ensure_schema(self) -> None:
        ddl = [
            """
            CREATE TABLE IF NOT EXISTS scrape_runs (
                id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                source_file VARCHAR(500) NOT NULL,
                started_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                finished_at DATETIME NULL,
                status VARCHAR(32) NOT NULL DEFAULT 'running',
                notes TEXT NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """,
            """
            CREATE TABLE IF NOT EXISTS company_queries (
                id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                run_id BIGINT UNSIGNED NOT NULL,
                query_order INT NOT NULL,
                company_name VARCHAR(500) NOT NULL,
                matched_search_term VARCHAR(500) NULL,
                searched_at DATETIME NULL,
                status VARCHAR(40) NOT NULL DEFAULT 'pending',
                captcha_attempts INT NOT NULL DEFAULT 0,
                result_count INT NOT NULL DEFAULT 0,
                raw_result_html MEDIUMTEXT NULL,
                error_message TEXT NULL,
                INDEX idx_company_queries_run (run_id),
                INDEX idx_company_queries_company (company_name(191))
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """,
            """
            CREATE TABLE IF NOT EXISTS establishments (
                est_id VARCHAR(64) NOT NULL PRIMARY KEY,
                establishment_name VARCHAR(500) NULL,
                address TEXT NULL,
                office_name VARCHAR(255) NULL,
                action_text VARCHAR(255) NULL,
                raw_row_json TEXT NULL,
                first_seen_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_seen_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """,
            """
            CREATE TABLE IF NOT EXISTS company_query_establishments (
                query_id BIGINT UNSIGNED NOT NULL,
                est_id VARCHAR(64) NOT NULL,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (query_id, est_id),
                INDEX idx_cqe_est_id (est_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """,
            """
            CREATE TABLE IF NOT EXISTS establishment_section_data (
                id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                run_id BIGINT UNSIGNED NOT NULL,
                query_id BIGINT UNSIGNED NULL,
                est_id VARCHAR(64) NOT NULL,
                section_name VARCHAR(100) NOT NULL,
                data_kind VARCHAR(20) NOT NULL,
                table_no INT NOT NULL,
                row_no INT NOT NULL,
                field_name VARCHAR(500) NULL,
                field_value MEDIUMTEXT NULL,
                row_json TEXT NULL,
                scraped_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_section_run_est (run_id, est_id),
                INDEX idx_section_name (section_name)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """,
            """
            CREATE TABLE IF NOT EXISTS payment_details (
                id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                est_id VARCHAR(64) NOT NULL,
                trrn VARCHAR(64) NULL,
                date_of_credit VARCHAR(64) NULL,
                amount VARCHAR(64) NULL,
                wage_month VARCHAR(32) NULL,
                no_of_employee VARCHAR(64) NULL,
                ecr VARCHAR(32) NULL,
                row_json TEXT NULL,
                scraped_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY uq_payment (est_id, trrn, wage_month, date_of_credit, amount),
                INDEX idx_payment_est_id (est_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """,
            """
            CREATE TABLE IF NOT EXISTS establishment_raw_pages (
                id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                run_id BIGINT UNSIGNED NOT NULL,
                est_id VARCHAR(64) NOT NULL,
                page_type VARCHAR(100) NOT NULL,
                raw_html MEDIUMTEXT NULL,
                scraped_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY uq_raw_page (run_id, est_id, page_type),
                INDEX idx_raw_pages_est_id (est_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """,
            """
            CREATE TABLE IF NOT EXISTS scrape_errors (
                id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                run_id BIGINT UNSIGNED NOT NULL,
                query_id BIGINT UNSIGNED NULL,
                est_id VARCHAR(64) NULL,
                stage VARCHAR(120) NOT NULL,
                error_message TEXT NOT NULL,
                traceback_text MEDIUMTEXT NULL,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_errors_run (run_id),
                INDEX idx_errors_est_id (est_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """,
        ]
        cursor = self.conn.cursor()
        for statement in ddl:
            cursor.execute(statement)
        self.migrate_schema(cursor)
        self.conn.commit()
        cursor.close()

    def migrate_schema(self, cursor: Any) -> None:
        migrations = [
            "ALTER TABLE establishments MODIFY est_id VARCHAR(64) NOT NULL",
            "ALTER TABLE company_query_establishments MODIFY est_id VARCHAR(64) NOT NULL",
            "ALTER TABLE establishment_section_data MODIFY est_id VARCHAR(64) NOT NULL",
            "ALTER TABLE payment_details MODIFY est_id VARCHAR(64) NOT NULL",
            "ALTER TABLE establishment_raw_pages MODIFY est_id VARCHAR(64) NOT NULL",
            "ALTER TABLE scrape_errors MODIFY est_id VARCHAR(64) NULL",
        ]
        for statement in migrations:
            cursor.execute(statement)
        cursor.execute("SHOW COLUMNS FROM company_queries LIKE 'matched_search_term'")
        if not cursor.fetchone():
            cursor.execute("ALTER TABLE company_queries ADD COLUMN matched_search_term VARCHAR(500) NULL AFTER company_name")

    def create_run(self, source_file: str, notes: str = "") -> int:
        cursor = self.conn.cursor()
        cursor.execute(
            "INSERT INTO scrape_runs (source_file, notes) VALUES (%s, %s)",
            (source_file, notes),
        )
        self.conn.commit()
        run_id = int(cursor.lastrowid)
        cursor.close()
        return run_id

    def finish_run(self, run_id: int, status: str, notes: str = "") -> None:
        cursor = self.conn.cursor()
        cursor.execute(
            "UPDATE scrape_runs SET finished_at = NOW(), status = %s, notes = CONCAT(COALESCE(notes, ''), %s) WHERE id = %s",
            (status, ("\n" + notes) if notes else "", run_id),
        )
        self.conn.commit()
        cursor.close()

    def create_company_query(self, run_id: int, query_order: int, company_name: str) -> int:
        cursor = self.conn.cursor()
        cursor.execute(
            "INSERT INTO company_queries (run_id, query_order, company_name) VALUES (%s, %s, %s)",
            (run_id, query_order, company_name),
        )
        self.conn.commit()
        query_id = int(cursor.lastrowid)
        cursor.close()
        return query_id

    def find_existing_company_status(self, company_name: str, statuses: set[str]) -> tuple[str, int] | None:
        if not statuses:
            return None
        placeholders = ", ".join(["%s"] * len(statuses))
        cursor = self.conn.cursor()
        cursor.execute(
            f"""
            SELECT status, result_count
            FROM company_queries
            WHERE company_name = %s AND status IN ({placeholders})
            ORDER BY run_id DESC, id DESC
            LIMIT 1
            """,
            (company_name, *sorted(statuses)),
        )
        row = cursor.fetchone()
        cursor.close()
        if not row:
            return None
        return str(row[0]), int(row[1] or 0)

    def update_company_query(
        self,
        query_id: int,
        status: str,
        captcha_attempts: int,
        result_count: int = 0,
        raw_result_html: str | None = None,
        error_message: str | None = None,
        matched_search_term: str | None = None,
    ) -> None:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            UPDATE company_queries
            SET searched_at = NOW(),
                status = %s,
                matched_search_term = COALESCE(%s, matched_search_term),
                captcha_attempts = %s,
                result_count = %s,
                raw_result_html = COALESCE(%s, raw_result_html),
                error_message = %s
            WHERE id = %s
            """,
            (status, matched_search_term, captcha_attempts, result_count, raw_result_html, error_message, query_id),
        )
        self.conn.commit()
        cursor.close()

    def save_search_result(self, query_id: int, result: SearchResult) -> None:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO establishments
                (est_id, establishment_name, address, office_name, action_text, raw_row_json)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                establishment_name = VALUES(establishment_name),
                address = VALUES(address),
                office_name = VALUES(office_name),
                action_text = VALUES(action_text),
                raw_row_json = VALUES(raw_row_json),
                last_seen_at = NOW()
            """,
            (
                result.est_id,
                result.establishment_name,
                result.address,
                result.office_name,
                result.action_text,
                json_dumps(result.raw_row),
            ),
        )
        cursor.execute(
            """
            INSERT IGNORE INTO company_query_establishments (query_id, est_id)
            VALUES (%s, %s)
            """,
            (query_id, result.est_id),
        )
        self.conn.commit()
        cursor.close()

    def save_raw_page(self, run_id: int, est_id: str, page_type: str, raw_html: str) -> None:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO establishment_raw_pages (run_id, est_id, page_type, raw_html)
            VALUES (%s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE raw_html = VALUES(raw_html), scraped_at = NOW()
            """,
            (run_id, est_id, page_type, raw_html),
        )
        self.conn.commit()
        cursor.close()

    def save_section_records(
        self,
        run_id: int,
        query_id: int,
        est_id: str,
        section_name: str,
        records: list[SectionRecord],
    ) -> None:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            DELETE FROM establishment_section_data
            WHERE run_id = %s AND est_id = %s AND section_name = %s
            """,
            (run_id, est_id, section_name),
        )
        if records:
            cursor.executemany(
                """
                INSERT INTO establishment_section_data
                    (run_id, query_id, est_id, section_name, data_kind, table_no, row_no,
                     field_name, field_value, row_json)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                [
                    (
                        run_id,
                        query_id,
                        est_id,
                        section_name,
                        record.data_kind,
                        record.table_no,
                        record.row_no,
                        record.field_name,
                        record.field_value,
                        json_dumps(record.row_json) if record.row_json is not None else None,
                    )
                    for record in records
                ],
            )
        self.conn.commit()
        cursor.close()

    def save_payment_rows(self, est_id: str, rows: list[dict[str, str]]) -> None:
        if not rows:
            return
        cursor = self.conn.cursor()
        cursor.executemany(
            """
            INSERT INTO payment_details
                (est_id, trrn, date_of_credit, amount, wage_month, no_of_employee, ecr, row_json)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                no_of_employee = VALUES(no_of_employee),
                ecr = VALUES(ecr),
                row_json = VALUES(row_json),
                scraped_at = NOW()
            """,
            [
                (
                    est_id,
                    row.get("trrn"),
                    row.get("date_of_credit"),
                    row.get("amount"),
                    row.get("wage_month"),
                    row.get("no_of_employee"),
                    row.get("ecr"),
                    json_dumps(row),
                )
                for row in rows
            ],
        )
        self.conn.commit()
        cursor.close()

    def save_error(
        self,
        run_id: int,
        query_id: int | None,
        est_id: str | None,
        stage: str,
        error_message: str,
        traceback_text: str | None = None,
    ) -> None:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO scrape_errors
                (run_id, query_id, est_id, stage, error_message, traceback_text)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (run_id, query_id, est_id, stage, error_message, traceback_text),
        )
        self.conn.commit()
        cursor.close()


def read_company_names(path: Path) -> list[str]:
    if not path.exists():
        raise FileNotFoundError(f"Company file not found: {path}")
    names: list[str] = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        names.append(value)
    return names


def open_captcha_file(path: Path) -> None:
    if not hasattr(os, "startfile"):
        return
    try:
        os.startfile(str(path))  # type: ignore[attr-defined]
    except OSError:
        pass


def close_captcha_image() -> None:
    if os.name == "nt":
        try:
            import subprocess
            for proc in ["Microsoft.Photos.exe", "PhotosApp.exe", "Photos.exe", "dllhost.exe"]:
                subprocess.run(["taskkill", "/F", "/IM", proc, "/T"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass


def prompt_captcha(company_name: str, search_term: str, captcha_path: Path, attempt: int, open_file: bool) -> str:
    print()
    print(f"Company: {company_name}")
    if search_term != company_name:
        print(f"Search term: {search_term}")
    print(f"CAPTCHA image: {captcha_path}")
    print("Type the CAPTCHA shown in the image. Type 'skip' to skip this company or 'quit' to stop.")

    if open_file:
        open_captcha_file(captcha_path)

    if not sys.stdin.isatty():
        print("CAPTCHA input is not available. Run epfo_scraper.py in an interactive terminal, not PM2/background.")
        return "quit"

    try:
        return input(f"CAPTCHA attempt {attempt}: ").strip()
    except EOFError:
        print("CAPTCHA input was closed. Run epfo_scraper.py in an interactive terminal, not PM2/background.")
        return "quit"


def scrape_establishment_details(
    base_client: EpfoClient,
    result: SearchResult,
    fetch_all_sections: bool,
) -> DetailFetchResult:
    client = base_client.clone()
    raw_pages: list[tuple[str, str]] = []
    sections: list[tuple[str, list[SectionRecord]]] = []
    payments: list[dict[str, str]] = []
    errors: list[tuple[str, str, str]] = []

    if not result.detail_urls:
        return DetailFetchResult(
            result.est_id,
            raw_pages,
            sections,
            payments,
            [("details", "No View Details URL found in search result.", "")],
        )

    urls_to_fetch = result.detail_urls if fetch_all_sections else result.detail_urls[:1]
    payment_url: str | None = None

    for index, url in enumerate(urls_to_fetch):
        section_name = SECTION_NAMES[index] if index < len(SECTION_NAMES) else f"detail_section_{index + 1}"
        try:
            section_html = client.fetch_detail_section(url, result.est_id)
            raw_pages.append((section_name, section_html))
            records = parse_detail_section(section_html)
            sections.append((section_name, records))
            if index == 0:
                payment_url = parse_payment_url(section_html)
        except Exception as exc:  # noqa: BLE001 - keep scraper running per establishment.
            errors.append((section_name, str(exc), traceback.format_exc()))

    if payment_url:
        try:
            payment_html = client.fetch_payment_details(payment_url, result.est_id)
            raw_pages.append(("payment_details", payment_html))
            payments = parse_payment_rows(payment_html)
        except Exception as exc:  # noqa: BLE001
            errors.append(("payment_details", str(exc), traceback.format_exc()))
    else:
        errors.append(("payment_details", "No View Payment Details URL found.", ""))

    return DetailFetchResult(result.est_id, raw_pages, sections, payments, errors)


def process_company(
    db: MySQLStore,
    client: EpfoClient,
    run_id: int,
    query_id: int,
    query_order: int,
    company_name: str,
    args: argparse.Namespace,
) -> tuple[int, int]:
    attempts = 0
    result_html: str | None = None
    search_results: list[SearchResult] = []
    matched_search_term: str | None = None
    variants = (
        company_search_variants(company_name, args.max_search_variants)
        if args.search_variants
        else [company_name]
    )

    for variant_index, search_term in enumerate(variants, start=1):
        variant_attempt = 0
        while True:
            captcha_path: Path | None = None
            if args.max_captcha_attempts > 0 and variant_attempt >= args.max_captcha_attempts:
                db.update_company_query(
                    query_id,
                    "failed",
                    attempts,
                    error_message=f"CAPTCHA attempts exhausted for search term: {search_term}",
                    matched_search_term=search_term,
                )
                close_captcha_image()
                return 0, 0

            variant_attempt += 1
            attempts += 1
            client.load_home()

            captcha_path = client.save_captcha(Path(args.captcha_dir), search_term, variant_attempt)
            captcha = prompt_captcha(company_name, search_term, captcha_path, variant_attempt, args.open_captcha)

            if captcha.lower() == "quit":
                close_captcha_image()
                raise KeyboardInterrupt
            if captcha.lower() == "skip":
                db.update_company_query(query_id, "skipped", attempts, error_message="Skipped by user.")
                close_captcha_image()
                return 0, 0
            if not captcha:
                print("Empty CAPTCHA. A new CAPTCHA will be loaded.")
                close_captcha_image()
                try:
                    captcha_path.unlink(missing_ok=True)
                except Exception:
                    pass
                continue

            try:
                result_html = client.search_establishment(search_term, captcha)
            except Exception as exc:  # noqa: BLE001
                db.save_error(run_id, query_id, None, "search", str(exc), traceback.format_exc())
                print(f"Search failed for {company_name} using '{search_term}': {exc}")
                close_captcha_image()
                if captcha_path is not None:
                    try:
                        captcha_path.unlink(missing_ok=True)
                    except Exception:
                        pass
                continue

            captcha_error = captcha_error_message(result_html)
            if captcha_error:
                print(f"{captcha_error} A new CAPTCHA will be loaded. This company will not continue until CAPTCHA is correct.")
                close_captcha_image()
                if captcha_path is not None:
                    try:
                        captcha_path.unlink(missing_ok=True)
                    except Exception:
                        pass
                continue

            # CAPTCHA successfully accepted by EPFO portal! Close image & cleanup temp file
            close_captcha_image()
            if captcha_path is not None:
                try:
                    captcha_path.unlink(missing_ok=True)
                except Exception:
                    pass

            search_results = parse_search_results(result_html)
            if search_results:
                matched_search_term = search_term
                break

            db.update_company_query(
                query_id,
                "searched",
                attempts,
                result_count=0,
                raw_result_html=result_html,
                matched_search_term=search_term,
            )
            if variant_index < len(variants):
                print(f"[{query_order}] No result for '{search_term}'. Trying another search term...")
            break

        if search_results:
            break

    if result_html is None:
        db.update_company_query(query_id, "failed", attempts, error_message="Search did not return a result.")
        return 0, 0

    final_captcha_error = captcha_error_message(result_html)
    if not search_results and final_captcha_error:
        db.update_company_query(query_id, "failed", attempts, raw_result_html=result_html, error_message=final_captcha_error)
        return 0, 0

    db.update_company_query(
        query_id,
        "searched",
        attempts,
        result_count=len(search_results),
        raw_result_html=result_html,
        matched_search_term=matched_search_term,
    )
    for search_result in search_results:
        db.save_search_result(query_id, search_result)

    if not search_results:
        tried = ", ".join(variants)
        db.update_company_query(
            query_id,
            "searched",
            attempts,
            result_count=0,
            raw_result_html=result_html,
            error_message=f"No establishments found. Tried: {tried}",
            matched_search_term=variants[-1] if variants else company_name,
        )
        print(f"[{query_order}] {company_name}: no establishments found. Tried: {tried}")
        return 0, 0

    suffix = f" using '{matched_search_term}'" if matched_search_term and matched_search_term != company_name else ""
    print(f"[{query_order}] {company_name}: {len(search_results)} establishments found{suffix}. Fetching details...")
    payment_count = 0

    if args.details_workers > 1 and len(search_results) > 1:
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(args.details_workers, len(search_results))) as executor:
            future_to_res = {
                executor.submit(scrape_establishment_details, client, res, args.fetch_all_sections): res
                for res in search_results
            }
            detail_results = [f.result() for f in concurrent.futures.as_completed(future_to_res)]
    else:
        detail_results = [scrape_establishment_details(client, res, args.fetch_all_sections) for res in search_results]

    for detail_result in detail_results:
        for page_type, raw_html in detail_result.raw_pages:
            db.save_raw_page(run_id, detail_result.est_id, page_type, raw_html)
        for section_name, records in detail_result.sections:
            db.save_section_records(run_id, query_id, detail_result.est_id, section_name, records)
        db.save_payment_rows(detail_result.est_id, detail_result.payments)
        payment_count += len(detail_result.payments)
        for stage, message, tb_text in detail_result.errors:
            db.save_error(run_id, query_id, detail_result.est_id, stage, message, tb_text)
        print(
            f"  {detail_result.est_id}: "
            f"{len(detail_result.sections)} sections, {len(detail_result.payments)} payments"
        )

    db.update_company_query(
        query_id,
        "completed",
        attempts,
        result_count=len(search_results),
        raw_result_html=result_html,
        matched_search_term=matched_search_term,
    )
    return len(search_results), payment_count


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scrape EPFO establishment search data into MySQL using official public portal AJAX endpoints."
    )
    parser.add_argument("--company-file", default="compony.txt", help="Text file containing one company name per line.")
    parser.add_argument("--start-at", type=int, default=1, help="1-based company row number to start from.")
    parser.add_argument("--limit", type=int, default=0, help="Maximum companies to process. 0 means no limit.")
    parser.add_argument(
        "--skip-existing-statuses",
        default="",
        help="Comma-separated company_queries statuses to skip if already present, e.g. completed,searched,skipped.",
    )
    parser.add_argument("--captcha-dir", default="captchas", help="Folder where CAPTCHA images are saved.")
    parser.add_argument(
        "--max-captcha-attempts",
        type=int,
        default=0,
        help="Manual CAPTCHA retries per search term. 0 means keep asking until CAPTCHA is correct.",
    )
    parser.add_argument("--details-workers", type=int, default=3, help="Parallel workers for establishment detail pages.")
    parser.add_argument("--delay", type=float, default=0.35, help="Delay between EPFO requests per worker.")
    parser.add_argument("--timeout", type=int, default=35, help="HTTP timeout in seconds.")
    parser.add_argument(
        "--max-search-variants",
        type=int,
        default=4,
        help="Maximum search terms to try per company when exact name has no result.",
    )
    parser.add_argument(
        "--no-search-variants",
        dest="search_variants",
        action="store_false",
        help="Only search the exact company name from the file.",
    )
    parser.add_argument(
        "--no-open-captcha",
        dest="open_captcha",
        action="store_false",
        help="Do not automatically open the saved CAPTCHA image.",
    )
    parser.add_argument(
        "--first-section-only",
        dest="fetch_all_sections",
        action="store_false",
        help="Fetch only the validity/payment section, not every View Details section.",
    )
    parser.set_defaults(open_captcha=True, fetch_all_sections=True, search_variants=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    company_file = Path(args.company_file)
    try:
        companies = read_company_names(company_file)
    except FileNotFoundError:
        print(f"Company file not found: {company_file}")
        print("Create compony.txt with one company name per line, or pass --company-file your_file.txt.")
        print("Example: copy compony.example.txt compony.txt")
        return 2
    if args.start_at < 1:
        args.start_at = 1
    if args.start_at > 1:
        companies = companies[args.start_at - 1 :]
    if args.limit and args.limit > 0:
        companies = companies[: args.limit]
    if not companies:
        print(f"No company names found in {company_file}. Add one company name per line and run again.")
        return 2

    try:
        db = MySQLStore()
    except mysql.connector.Error as exc:
        print(f"Database connection failed: {exc}")
        print("Check .env database values, TiDB/MySQL network access, firewall allowlist, and outbound port 4000/3306.")
        print("If you are running on aaPanel/server, make sure the server IP is allowed by TiDB and the host can reach the DB.")
        return 2

    client = EpfoClient(timeout=args.timeout, delay=args.delay)
    skip_statuses = {
        status.strip().lower()
        for status in args.skip_existing_statuses.split(",")
        if status.strip()
    }
    run_id = db.create_run(
        str(company_file.resolve()),
        notes=f"Companies: {len(companies)}; start_at: {args.start_at}; limit: {args.limit or 'none'}",
    )

    total_establishments = 0
    total_payments = 0
    skipped_existing = 0
    try:
        for index, company_name in enumerate(companies, start=1):
            source_index = args.start_at + index - 1
            existing = db.find_existing_company_status(company_name, skip_statuses)
            if existing:
                skipped_existing += 1
                status, result_count = existing
                print(f"[{source_index}] {company_name}: skipped existing status={status}, result_count={result_count}")
                continue
            query_id = db.create_company_query(run_id, source_index, company_name)
            try:
                found, payments = process_company(db, client, run_id, query_id, source_index, company_name, args)
                total_establishments += found
                total_payments += payments
            except KeyboardInterrupt:
                db.update_company_query(query_id, "stopped", 0, error_message="Stopped by user.")
                raise
            except Exception as exc:  # noqa: BLE001
                db.update_company_query(query_id, "failed", 0, error_message=str(exc))
                db.save_error(run_id, query_id, None, "company", str(exc), traceback.format_exc())
                print(f"[{index}] {company_name}: failed: {exc}")

        db.finish_run(
            run_id,
            "completed",
            notes=(
                f"Total establishments: {total_establishments}; total payment rows: {total_payments}; "
                f"skipped existing: {skipped_existing}"
            ),
        )
        print()
        print(f"Done. Run ID: {run_id}")
        print(f"Establishments saved: {total_establishments}")
        print(f"Payment rows saved: {total_payments}")
        print(f"Skipped existing: {skipped_existing}")
        return 0
    except KeyboardInterrupt:
        db.finish_run(
            run_id,
            "stopped",
            notes=(
                f"Stopped. Establishments saved: {total_establishments}; payment rows saved: {total_payments}; "
                f"skipped existing: {skipped_existing}"
            ),
        )
        print("\nStopped by user.")
        return 130
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
