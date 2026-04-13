# Pling Upload Findings And Plan

This repository can already build a distributable KDE Plasma widget package with
`make package`. The missing piece is publishing the generated `.plasmoid` file
to Pling/OpenDesktop as part of a release workflow.

The safest route is to own a small uploader script instead of depending on the
unofficial `pling-publisher` package. A Python implementation using `niquests`
should be possible without Playwright for the normal path.

## Repository Context

- The package artifact is produced by `make package`.
- The expected output is `build/org.kde.plasma.starter.plasmoid`, derived from
  `package/metadata.json`.
- There is currently no GitHub Actions workflow in this repository.
- The package includes a compiled Qt/QML C++ module, so CI needs a runner with
  the right KDE/Qt build dependencies.
- `package/metadata.json` currently declares version `1.0`; release automation
  should decide whether the source of truth is the Git tag, this metadata file,
  or both.

## Pling/OpenDesktop Findings

### Official API

There does not appear to be a supported official upload API for editing content
files on Pling/OpenDesktop.

A recent OpenDesktop forum answer says the OCS API is effectively read-only for
this use case and that content-edit upload support is not implemented:

- https://forum.opendesktop.org/t/ocs-api-content-edit-unknown-request/20947

An older CI/CD thread also points to there being no built-in continuous
deployment integration for Pling:

- https://forum.opendesktop.org/t/there-is-a-way-to-setup-a-continuous-deployment-from-a-app-in-pling/18876

So this should be treated as browser/API automation of the existing web app, not
as use of a stable public publishing API.

### Existing Unofficial Publisher

`pling-publisher` is an unofficial Python package:

- https://pypi.org/project/pling-publisher/
- https://github.com/dmzoneill/pling-publisher

Its general approach is useful as prior art, but I would not make this
repository depend on it. It logs in with a session, scrapes edit-page values, and
posts multipart upload data to Pling's internal upload endpoints. Owning a small
script gives us better control over:

- dependency trust
- logging and secret handling
- failure behavior
- compatibility fixes when Pling changes its pages
- dry-run and CI behavior

### Current Web Flow

Pling redirects to OpenDesktop for the account and product edit flow. The
canonical base URL should be:

```text
https://www.opendesktop.org
```

The login page is a normal HTML form with these important fields:

- `email`
- `password`
- `redirect_url`
- `csrf`
- `twit`

The site also sets and checks bot-detection signals. In local testing, a normal
browser-like request could fetch the login form, and a fake login returned the
expected "incorrect login" page rather than a hard block. That means a
`niquests.Session` implementation is likely viable today.

The current first-party upload UI posts files through product controller
endpoints, not only through raw ppload internals. KDE's hosted source shows the
relevant behavior here:

- https://lxr.kde.org/source/webapps/ocs-webserver/application/modules/default/views/scripts/user/products.phtml
- https://lxr.kde.org/source/webapps/ocs-webserver/application/modules/default/controllers/ProductController.php

Important upload/edit endpoints are exposed as HTML data attributes on the edit
page, especially around the upload modal:

- `data-addpploadfile-uri`
- `data-updatepploadfile-uri`
- `data-deletepploadfile-uri`
- `data-deletepploadfiles-uri`
- `data-product-id`
- `data-ppload-collection-id`

The server-side flow expects a multipart field named `file_upload`.

The practical shape is:

1. Log in.
2. Fetch the product edit page.
3. Parse the upload endpoint URLs from the page.
4. POST the `.plasmoid` artifact as `file_upload`.
5. Read the JSON response.
6. Optionally POST metadata such as version, description, compatibility flags,
   category, and tags.
7. Optionally delete/archive older uploaded files.

## Is This A Playwright Automation?

Not as the first choice.

The recommended implementation is HTTP automation with `niquests`, plus an HTML
parser such as `selectolax`, `beautifulsoup4`, or `lxml`.

Playwright should be kept as a fallback or diagnostic tool because:

- the normal upload flow is form posts and JSON responses
- HTTP automation is easier to run in CI
- HTTP automation is easier to test and log safely
- Playwright/headless browsers are more likely to trigger bot checks
- Playwright can accidentally become a brittle click-script tied to UI layout

Playwright may become necessary if Pling changes the flow to require a real
browser-only challenge, but the script should fail closed rather than trying to
bypass CAPTCHA, 2FA, or anti-bot checks.

## Recommended Python Design

Create a small repository-owned uploader, for example:

```text
scripts/pling_upload.py
```

Use:

- `niquests.Session` for cookies and requests
- an HTML parser for CSRF and endpoint discovery
- `argparse` or `typer` for CLI arguments
- explicit dry-run support
- structured, redacted logging

Suggested inputs:

```text
PLING_USERNAME
PLING_PASSWORD
PLING_PROJECT_ID
PLING_BASE_URL=https://www.opendesktop.org
PLING_DELETE_OLD_FILES=false
```

Suggested CLI:

```text
python scripts/pling_upload.py \
  --artifact build/org.kde.plasma.starter.plasmoid \
  --project-id "$PLING_PROJECT_ID" \
  --version "$VERSION" \
  --description-file RELEASE_NOTES.md
```

Suggested dry run:

```text
python scripts/pling_upload.py \
  --project-id "$PLING_PROJECT_ID" \
  --dry-run
```

Dry run should:

- log in
- fetch the product edit page
- parse upload endpoints
- optionally list existing files
- avoid uploading or deleting anything

## Recommended HTTP Flow

### 1. Start A Session

Use a browser-like user agent and keep redirects enabled.

Set conservative headers:

```text
User-Agent: Mozilla/5.0 ...
Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8
Accept-Language: en-US,en;q=0.9
```

The site currently uses a `verified=1` cookie in its browser-side checks. The
script can set it before login, but should not rely on that as a stable contract.

### 2. Fetch Login Page

GET:

```text
https://www.opendesktop.org/login/
```

Parse hidden form fields, especially:

- `csrf`
- `redirect_url`
- `twit`

### 3. Submit Login

POST the login form with:

- `email`
- `password`
- parsed hidden fields

After login, verify success by checking that the session can access the product
edit page. Do not treat HTTP 200 alone as success.

### 4. Fetch Product Edit Page

GET:

```text
https://www.opendesktop.org/p/<project_id>/edit
```

Parse upload-related data attributes from the page. Fail clearly if any required
endpoint is missing.

### 5. Upload Artifact

POST multipart form data to the parsed `data-addpploadfile-uri` endpoint.

Use multipart field:

```text
file_upload
```

Recommended headers:

```text
X-Requested-With: XMLHttpRequest
Accept: application/json
Referer: https://www.opendesktop.org/p/<project_id>/edit
```

Require JSON with:

```text
status: ok
```

Capture the returned file id and collection id.

### 6. Update File Metadata

POST to the parsed `data-updatepploadfile-uri` endpoint with:

- `file_id`
- `file_version`
- `file_description`
- optional `file_category`
- optional `file_tags`
- optional `ocs_compatible=1`

This step should also require JSON `status: ok`.

### 7. Optional Cleanup

Old file cleanup should be opt-in. It is useful, but destructive.

The script can list files via the edit page's file listing endpoint and then
POST old file ids to `data-deletepploadfile-uri`. Default behavior should be to
keep all uploaded files until the workflow has been proven on a test product.

## CI/CD Plan

### Stage 1: GitHub Release Artifact

First add CI that builds the `.plasmoid` and attaches it to a GitHub Release.
This is the most reliable publishing target and gives users a fallback if Pling
upload fails.

Trigger:

```text
on:
  release:
    types: [published]
```

or tag push:

```text
on:
  push:
    tags:
      - "v*"
```

### Stage 2: Manual Pling Dry Run

Add a manual `workflow_dispatch` job that only logs in, parses the edit page,
and reports whether upload endpoints are available.

This validates secrets and Pling compatibility without publishing anything.

### Stage 3: Manual Test Upload

Run the uploader manually against either:

- a disposable Pling test product, or
- the real product with old-file cleanup disabled

Verify the uploaded file manually from the Pling UI.

### Stage 4: Release Upload

Enable automatic Pling upload after GitHub release artifact creation succeeds.

The release workflow should:

1. build the package
2. upload it to the GitHub Release
3. upload it to Pling
4. fail visibly if Pling upload fails

The workflow should not delete old Pling files by default.

### Stage 5: Optional Old-File Policy

After the upload path is stable, decide whether to:

- keep all historical files
- keep the latest N files
- delete/archive all files older than the current release

This should be a separate explicit setting, not a default.

## Security Notes

- Store credentials only as GitHub Actions secrets.
- Prefer a dedicated Pling/OpenDesktop account if project ownership allows it.
- Do not log passwords, cookies, CSRF tokens, or full response bodies from
  authenticated pages.
- Mask usernames/project ids in logs if desired.
- Do not attempt to bypass CAPTCHA, 2FA, or stronger anti-bot challenges.
- Keep upload retries conservative to avoid account lockouts.
- Use GitHub protected environments if releases should require approval before
  publishing to Pling.

## Failure Modes To Expect

- Login succeeds locally but fails in GitHub Actions because of bot checks.
- The edit page HTML changes and endpoint parsing breaks.
- Pling/OpenDesktop redirects domains differently.
- The upload succeeds but metadata update fails.
- The account lacks permission to edit the product id.
- The file upload endpoint returns HTML instead of JSON on auth failure.
- Deleting old files removes something that should have stayed available.

Each of these should produce a clear, non-secret error message and stop the
workflow.

## Open Questions

- Should the release version come from the Git tag or `package/metadata.json`?
- Should the Pling file description come from GitHub release notes, a checked-in
  changelog, or a static text field?
- Should old Pling files be kept forever at first?
- Is there a separate Pling test product available for the first upload?
- Does the real product need special GHNS/OCS compatibility metadata beyond
  `ocs_compatible=1`?

## Recommendation

Implement a repository-owned `niquests` uploader first, not Playwright.

Treat Pling upload as a best-effort release publishing step built on top of the
current web UI. Keep GitHub Releases as the reliable primary artifact host, then
publish to Pling after the artifact exists. Add dry-run and manual test upload
before making Pling upload automatic on release.
