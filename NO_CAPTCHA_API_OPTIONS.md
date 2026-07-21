# No-Manual-CAPTCHA EPFO API Options

## Important

The official EPFO public establishment search portal is CAPTCHA-protected. The project must not try to bypass that CAPTCHA.

Current official portal endpoint used by the scraper:

```text
POST https://unifiedportal-emp.epfindia.gov.in/publicPortal/no-auth/estSearch/searchEstablishment?_HDIV_STATE_=...
```

This endpoint requires:

```json
{
  "EstName": "company name",
  "EstCode": "",
  "captcha": "manual captcha"
}
```

## Legitimate No-Manual-CAPTCHA Alternatives Found

These are not official EPFO public APIs. They are third-party verification/KYC APIs and require account credentials/tokens.

### 1. Surepass - Establishment Name To Details

Best fit when input is company/establishment name.

```text
POST https://kyc-api.surepass.app/api/v1/epfo/establishment-name-to-details
Authorization: Bearer <token>
Content-Type: application/json
```

Use when:

- You have only company names from `IN Companies.xlsx`.
- You want establishment details and payment history without manually typing CAPTCHA.

Needs:

- Surepass API account.
- Bearer token.

### 2. Surepass - Establishment Details By Establishment ID

Best fit when EPFO Establishment ID is already known.

```text
POST https://kyc-api.surepass.app/api/v1/epfo/establishment-details
Authorization: Bearer <token>
Content-Type: application/json
```

Example body:

```json
{
  "establishment_id": "DLCPM0001014111"
}
```

Response includes:

- validity status
- establishment details
- additional information including CIN/ESIC/LIN
- payment details
- director details

### 3. Surepass Async EPFO Establishment APIs

Useful for bulk/high-volume jobs.

Initialize:

```text
POST https://kyc-api.surepass.app/api/v1/epfo-async/establishment-details/initialize
```

Status:

```text
POST https://kyc-api.surepass.app/api/v1/epfo-async/establishment-details/status
```

### 4. IndiConnect - EPFO Basic Establishment Search

Uses GraphQL and requires provider credentials.

```text
POST https://api.staging.indiconnect.in/idverifygr/verification
```

Needs headers:

- `myAppId`
- `service-key`
- `Authorization`
- `Content-Type`

Input requires EPFO establishment ID.

## Recommended Path For This Project

For your `IN Companies.xlsx` workflow, Surepass `establishment-name-to-details` is the closest fit because it accepts establishment/company name.

After you provide a Surepass Bearer token, add this environment variable:

```powershell
$env:SUREPASS_TOKEN="your_token_here"
```

Then the project can be extended with an API mode:

```powershell
python epfo_api_importer.py --company-file all_companies.txt --provider surepass
```

The importer should map the API response into the existing MySQL/TiDB tables:

- `company_queries`
- `establishments`
- `company_query_establishments`
- `establishment_section_data`
- `payment_details`
- `scrape_errors`
