#!/usr/bin/env python3
"""Dry-run Pling/OpenDesktop uploader probe.

Phase 1 intentionally supports dry-run validation only:
- authenticate
- open product edit page
- parse required upload endpoint attributes

It does not upload, update, or delete files.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Dict, Iterable, Optional
from urllib.parse import urljoin, urlparse

try:
    import niquests
except ImportError as exc:  # pragma: no cover - import guard
    print(
        "ERROR: missing dependency 'niquests'. Install it first, e.g. 'pip install niquests'.",
        file=sys.stderr,
    )
    raise SystemExit(2) from exc


DEFAULT_BASE_URL = "https://www.opendesktop.org"
LOGIN_PATH = "/login/"
EDIT_PATH_TEMPLATE = "/p/{project_id}/edit"
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_MAX_RETRIES = 2

REQUIRED_EDIT_DATA_ATTRS = (
    "data-addpploadfile-uri",
    "data-updatepploadfile-uri",
    "data-deletepploadfile-uri",
    "data-deletepploadfiles-uri",
    "data-product-id",
    "data-ppload-collection-id",
)

SENSITIVE_KEYS = {
    "password",
    "csrf",
    "cookie",
    "cookies",
    "authorization",
    "token",
    "session",
}


class PlingDryRunError(RuntimeError):
    """A non-secret, user-facing dry-run failure."""


@dataclass(frozen=True)
class LoginForm:
    action_url: str
    hidden_fields: Dict[str, str]


class _LoginFormParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._inside_form = False
        self._form_action: Optional[str] = None
        self._form_has_auth_fields = False
        self._form_hidden_inputs: Dict[str, str] = {}
        self.login_form: Optional[LoginForm] = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        attrs_dict = {k: (v or "") for k, v in attrs}

        if tag == "form":
            self._inside_form = True
            self._form_action = attrs_dict.get("action") or LOGIN_PATH
            self._form_has_auth_fields = False
            self._form_hidden_inputs = {}
            return

        if not self._inside_form or tag != "input":
            return

        name = (attrs_dict.get("name") or "").strip()
        input_type = (attrs_dict.get("type") or "").strip().lower()
        value = attrs_dict.get("value") or ""

        if name in {"email", "password"}:
            self._form_has_auth_fields = True

        if input_type == "hidden" and name:
            self._form_hidden_inputs[name] = value

    def handle_endtag(self, tag: str) -> None:
        if tag != "form" or not self._inside_form:
            return

        if self._form_has_auth_fields:
            self.login_form = LoginForm(
                action_url=self._form_action or LOGIN_PATH,
                hidden_fields=dict(self._form_hidden_inputs),
            )

        self._inside_form = False
        self._form_action = None
        self._form_has_auth_fields = False
        self._form_hidden_inputs = {}


def _redact(key: str, value: object) -> object:
    lowered = key.lower()
    if any(secret in lowered for secret in SENSITIVE_KEYS):
        return "***"
    return value


def log_event(level: str, event: str, **fields: object) -> None:
    payload = {
        "level": level.upper(),
        "event": event,
    }
    payload.update({k: _redact(k, v) for k, v in fields.items()})
    print(json.dumps(payload, ensure_ascii=True, sort_keys=True))


def normalize_base_url(base_url: str) -> str:
    normalized = base_url.strip().rstrip("/")
    parsed = urlparse(normalized)
    if not parsed.scheme or not parsed.netloc:
        raise PlingDryRunError(f"Invalid --base-url: {base_url!r}")
    return normalized


def _safe_response_context(response: object) -> dict[str, object]:
    status_code = getattr(response, "status_code", "unknown")
    url = getattr(response, "url", "unknown")
    return {"status_code": status_code, "url": str(url)}


def request_with_retries(
    session: "niquests.Session",
    method: str,
    url: str,
    *,
    timeout: float,
    max_retries: int,
    retry_on_statuses: Iterable[int] = (429, 500, 502, 503, 504),
    **kwargs: object,
):
    attempt = 0
    last_error: Optional[Exception] = None

    while attempt <= max_retries:
        attempt += 1
        try:
            response = session.request(method=method, url=url, timeout=timeout, **kwargs)
        except Exception as exc:
            last_error = exc
            log_event(
                "warning",
                "http_request_exception",
                method=method,
                url=url,
                attempt=attempt,
                max_retries=max_retries,
                error_type=type(exc).__name__,
            )
            if attempt > max_retries:
                break
            time.sleep(min(0.5 * attempt, 2.0))
            continue

        if response.status_code in retry_on_statuses and attempt <= max_retries:
            log_event(
                "warning",
                "http_retryable_status",
                method=method,
                url=url,
                attempt=attempt,
                status_code=response.status_code,
            )
            time.sleep(min(0.5 * attempt, 2.0))
            continue

        return response

    if last_error is not None:
        raise PlingDryRunError(
            f"HTTP request failed after retries: {method} {url} ({type(last_error).__name__})"
        ) from last_error

    raise PlingDryRunError(f"HTTP request failed after retries: {method} {url}")


def parse_login_form(login_html: str, login_url: str) -> LoginForm:
    parser = _LoginFormParser()
    parser.feed(login_html)

    if parser.login_form is None:
        raise PlingDryRunError("Could not find login form with email/password fields.")

    action_url = urljoin(login_url, parser.login_form.action_url)
    hidden_fields = parser.login_form.hidden_fields

    # csrf is required for current flow. redirect_url/twit can be empty but should exist.
    missing_keys = [k for k in ("csrf", "redirect_url", "twit") if k not in hidden_fields]
    if missing_keys:
        raise PlingDryRunError(
            f"Login form is missing expected hidden fields: {', '.join(missing_keys)}"
        )

    return LoginForm(action_url=action_url, hidden_fields=hidden_fields)


def extract_required_data_attrs(edit_html: str, project_id: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for attr in REQUIRED_EDIT_DATA_ATTRS:
        match = re.search(rf"\b{re.escape(attr)}=[\"']([^\"']*)[\"']", edit_html)
        if match:
            attrs[attr] = match.group(1).strip()

    missing = [attr for attr in REQUIRED_EDIT_DATA_ATTRS if attr not in attrs]
    if missing:
        raise PlingDryRunError(
            "Edit page is missing required upload data attributes: " + ", ".join(missing)
        )

    # Some edit pages leave data-product-id blank even when the page is valid.
    if attrs.get("data-product-id", "") == "":
        attrs["data-product-id"] = project_id

    return attrs


def looks_like_login_page(html: str) -> bool:
    lowered = html.lower()
    return (
        'name="email"' in lowered
        and 'name="password"' in lowered
        and "login" in lowered
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Dry-run checker for Pling/OpenDesktop product upload endpoints."
    )
    parser.add_argument(
        "--project-id",
        help="Pling/OpenDesktop project id (fallback: env PLING_PROJECT_ID)",
    )
    parser.add_argument(
        "--base-url",
        help=f"Base URL (fallback: env PLING_BASE_URL, then {DEFAULT_BASE_URL})",
    )
    parser.add_argument(
        "--username",
        help="Login username/email (fallback: env PLING_USERNAME)",
    )
    parser.add_argument(
        "--password",
        help="Login password (fallback: env PLING_PASSWORD)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        help=(
            "Per-request timeout seconds "
            f"(fallback: env PLING_TIMEOUT, then {DEFAULT_TIMEOUT_SECONDS})"
        ),
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        help=(
            "Retry count for transient errors "
            f"(fallback: env PLING_MAX_RETRIES, then {DEFAULT_MAX_RETRIES})"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Required in Phase 1. Validates login + endpoint discovery only.",
    )
    return parser


def resolve_cli_or_env(
    *,
    cli_value: Optional[str],
    env_key: str,
    default: Optional[str] = None,
) -> Optional[str]:
    if cli_value is not None and cli_value.strip() != "":
        return cli_value.strip()

    env_value = os.getenv(env_key)
    if env_value is not None and env_value.strip() != "":
        return env_value.strip()

    return default


def resolve_float_cli_or_env(
    *,
    cli_value: Optional[float],
    env_key: str,
    default: float,
) -> float:
    if cli_value is not None:
        return cli_value

    env_value = os.getenv(env_key)
    if env_value is None or env_value.strip() == "":
        return default

    try:
        return float(env_value)
    except ValueError as exc:
        raise PlingDryRunError(
            f"Invalid {env_key} value {env_value!r}; expected a number."
        ) from exc


def resolve_int_cli_or_env(
    *,
    cli_value: Optional[int],
    env_key: str,
    default: int,
) -> int:
    if cli_value is not None:
        return cli_value

    env_value = os.getenv(env_key)
    if env_value is None or env_value.strip() == "":
        return default

    try:
        return int(env_value)
    except ValueError as exc:
        raise PlingDryRunError(
            f"Invalid {env_key} value {env_value!r}; expected an integer."
        ) from exc


def run_dry_run(args: argparse.Namespace) -> int:
    if not args.dry_run:
        raise PlingDryRunError(
            "Phase 1 supports dry-run only. Re-run with --dry-run."
        )

    project_id = resolve_cli_or_env(cli_value=args.project_id, env_key="PLING_PROJECT_ID")
    if not project_id:
        raise PlingDryRunError(
            "Missing project id. Set --project-id or env PLING_PROJECT_ID."
        )

    username = resolve_cli_or_env(cli_value=args.username, env_key="PLING_USERNAME")
    password = resolve_cli_or_env(cli_value=args.password, env_key="PLING_PASSWORD")
    if not username or not password:
        raise PlingDryRunError(
            "Missing credentials. Set --username/--password or env PLING_USERNAME/PLING_PASSWORD."
        )

    timeout = resolve_float_cli_or_env(
        cli_value=args.timeout,
        env_key="PLING_TIMEOUT",
        default=DEFAULT_TIMEOUT_SECONDS,
    )
    max_retries = resolve_int_cli_or_env(
        cli_value=args.max_retries,
        env_key="PLING_MAX_RETRIES",
        default=DEFAULT_MAX_RETRIES,
    )

    if max_retries < 0:
        raise PlingDryRunError("--max-retries must be >= 0")

    if timeout <= 0:
        raise PlingDryRunError("--timeout must be > 0")

    base_url = normalize_base_url(
        resolve_cli_or_env(
            cli_value=args.base_url,
            env_key="PLING_BASE_URL",
            default=DEFAULT_BASE_URL,
        )
        or DEFAULT_BASE_URL
    )
    login_url = urljoin(base_url + "/", LOGIN_PATH.lstrip("/"))
    edit_url = urljoin(
        base_url + "/",
        EDIT_PATH_TEMPLATE.format(project_id=project_id).lstrip("/"),
    )

    session = niquests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/123.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
    )
    # Seen in current browser-side checks; harmless if ignored server-side.
    session.cookies.set("verified", "1", domain=urlparse(base_url).hostname)

    log_event("info", "dry_run_start", base_url=base_url, project_id=project_id)

    login_page = request_with_retries(
        session,
        "GET",
        login_url,
        timeout=timeout,
        max_retries=max_retries,
    )
    log_event("info", "login_page_fetched", **_safe_response_context(login_page))
    login_form = parse_login_form(login_page.text, login_url)

    form_payload = dict(login_form.hidden_fields)
    form_payload.update({"email": username, "password": password})

    auth_response = request_with_retries(
        session,
        "POST",
        login_form.action_url,
        data=form_payload,
        timeout=timeout,
        max_retries=max_retries,
    )
    log_event("info", "login_submitted", **_safe_response_context(auth_response))

    # Treat clear auth failure content as terminal immediately.
    if "incorrect login" in auth_response.text.lower():
        raise PlingDryRunError("Authentication failed: incorrect username or password.")

    edit_page = request_with_retries(
        session,
        "GET",
        edit_url,
        timeout=timeout,
        max_retries=max_retries,
    )
    log_event("info", "edit_page_fetched", **_safe_response_context(edit_page))

    if looks_like_login_page(edit_page.text):
        raise PlingDryRunError(
            "Authentication appears unsuccessful: redirected back to login page."
        )

    if str(edit_page.url).rstrip("/") == normalize_base_url(login_url).rstrip("/"):
        raise PlingDryRunError("Unable to access edit page; received login page URL.")

    attrs = extract_required_data_attrs(edit_page.text, project_id=project_id)
    normalized_attrs: dict[str, str] = {}
    for key, value in attrs.items():
        resolved_value = value.replace("@@project_id@@", project_id)
        if key.endswith("-uri"):
            normalized_attrs[key] = urljoin(base_url + "/", resolved_value.lstrip("/"))
        else:
            normalized_attrs[key] = resolved_value

    # Endpoint inventory only (safe to log); no mutation calls are performed in Phase 1.
    for key in REQUIRED_EDIT_DATA_ATTRS:
        log_event("info", "endpoint_discovered", name=key, value=normalized_attrs[key])

    log_event(
        "info",
        "dry_run_success",
        message="Dry-run completed. No upload/update/delete actions were performed.",
    )
    return 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        return run_dry_run(args)
    except PlingDryRunError as exc:
        log_event("error", "dry_run_failed", message=str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
