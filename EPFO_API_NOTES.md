# EPFO Public Portal API Notes

These are the portal calls used by `epfo_scraper.py`. Do not hardcode `_HDIV_STATE_` values because EPFO generates them dynamically for each browser/session.

## Search Establishment

Found in Chrome DevTools:

```text
Network > Fetch/XHR > searchEstablishment?... > Headers
```

From the screenshot:

```text
POST https://unifiedportal-emp.epfindia.gov.in/publicPortal/no-auth/estSearch/searchEstablishment?_HDIV_STATE_=...
```

Payload:

```json
{
  "EstName": "ganesh roadlines private limited",
  "EstCode": "",
  "captcha": "USER_TYPED_CAPTCHA"
}
```

Response:

```text
HTML table containing Establishment ID, Establishment Name, Address, Office Name, and View Details/View Report actions.
```

## CAPTCHA

Initial image:

```text
GET /publicPortal/no-auth/captcha/createCaptcha?_HDIV_STATE_=...
```

Reload image:

```text
GET /publicPortal/no-auth/ecr/loadCaptchaPage?_HDIV_STATE_=...
```

CAPTCHA must be entered manually. The scraper saves/opens the image and asks in the terminal.

## View Details

After search response, the `View Details` link contains dynamic POST URLs like:

```text
POST /publicPortal/no-auth/estSearch/getDetails_3?_HDIV_STATE_=...
POST /publicPortal/no-auth/estSearch/getDetails_4?_HDIV_STATE_=...
...
POST /publicPortal/no-auth/estSearch/getDetails_12?_HDIV_STATE_=...
```

Payload:

```json
{
  "EstId": "GNRTK2482446000"
}
```

Response:

```text
HTML sections for validity status, EPFO master details, Form 5A details, owner/director details, additional information, etc.
```

## Payment Details

The payment details URL is inside the `getDetails_3` response when EPFO provides it.

Payload:

```json
{
  "EstId": "GNRTK2482446000"
}
```

Response:

```text
HTML payment table with TRRN, Date Of Credit, Amount, Wage Month, No. of Employee, and ECR.
```

## Current Project Handling

`epfo_scraper.py` already:

- Loads the home page.
- Parses fresh dynamic `_HDIV_STATE_` URLs.
- Downloads CAPTCHA image.
- Sends `searchEstablishment` POST.
- Parses establishment rows.
- Sends all `getDetails_*` POST calls.
- Finds and fetches payment details.
- Stores everything into MySQL/TiDB tables.
