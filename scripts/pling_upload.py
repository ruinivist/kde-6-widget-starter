#!/usr/bin/env python3
"""Pling/OpenDesktop uploader.

Modes:
- default: destructive overwrite upload (delete all product files, then upload one)
- --dry-run: authenticate and validate upload endpoint discovery only
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
from pathlib import Path
from typing import Dict, Iterable, Optional
from urllib.parse import urljoin, urlparse

try:
    import niquests
except ImportError as exc:  # pragma: no cover - import guard
    print(
        "ERROR: missing dependency 'niquests'. Install it first, e.g. 'uv add niquests'.",
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


class PlingUploaderError(RuntimeError):
    """A non-secret, user-facing uploader failure."""


@dataclass(frozen=True)
class LoginForm:
    action_url: str
    hidden_fields: Dict[str, str]


@dataclass(frozen=True)
class RuntimeConfig:
    project_id: str
    base_url: str
    username: str
    password: str
    timeout: float
    max_retries: int
    dry_run: bool
    artifact_path: Optional[Path]


@dataclass(frozen=True)
class EditContext:
    add_file_url: str
    update_file_url: str
    delete_file_url: str
    delete_all_files_url: str
    product_id: str
    collection_id: str
    file_server_upload_url: str
    file_server_client_id: str
    file_server_owner_id: str


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
        raise PlingUploaderError(f"Invalid --base-url: {base_url!r}")
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
        raise PlingUploaderError(
            f"HTTP request failed after retries: {method} {url} ({type(last_error).__name__})"
        ) from last_error

    raise PlingUploaderError(f"HTTP request failed after retries: {method} {url}")


def parse_json_response(response: object, *, context: str) -> dict[str, object]:
    try:
        data = response.json()  # type: ignore[attr-defined]
    except Exception as exc:
        raise PlingUploaderError(f"{context}: expected JSON response.") from exc

    if not isinstance(data, dict):
        raise PlingUploaderError(f"{context}: response JSON shape is not an object.")

    return data


def parse_login_form(login_html: str, login_url: str) -> LoginForm:
    parser = _LoginFormParser()
    parser.feed(login_html)

    if parser.login_form is None:
        raise PlingUploaderError("Could not find login form with email/password fields.")

    action_url = urljoin(login_url, parser.login_form.action_url)
    hidden_fields = parser.login_form.hidden_fields

    missing_keys = [k for k in ("csrf", "redirect_url", "twit") if k not in hidden_fields]
    if missing_keys:
        raise PlingUploaderError(
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
        raise PlingUploaderError(
            "Edit page is missing required upload data attributes: " + ", ".join(missing)
        )

    if attrs.get("data-product-id", "") == "":
        attrs["data-product-id"] = project_id

    return attrs


def extract_js_string_var(html: str, var_name: str) -> Optional[str]:
    match = re.search(rf"\bvar\s+{re.escape(var_name)}\s*=\s*[\"']([^\"']+)[\"']", html)
    if match:
        return match.group(1).strip()
    return None


def extract_form_append_literal(html: str, key: str) -> Optional[str]:
    match = re.search(
        rf"form\.append\([\"']{re.escape(key)}[\"'],\s*[\"']([^\"']+)[\"']\)",
        html,
    )
    if match:
        return match.group(1).strip()
    return None


def looks_like_login_page(html: str) -> bool:
    lowered = html.lower()
    return 'name="email"' in lowered and 'name="password"' in lowered and "login" in lowered


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Pling/OpenDesktop uploader. Default mode deletes all files then uploads artifact."
        )
    )
    parser.add_argument(
        "--artifact",
        help="Artifact path for upload mode (fallback: env PLING_ARTIFACT)",
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
        help="Validate login and endpoint discovery only.",
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
        raise PlingUploaderError(
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
        raise PlingUploaderError(
            f"Invalid {env_key} value {env_value!r}; expected an integer."
        ) from exc


def resolve_runtime_config(args: argparse.Namespace) -> RuntimeConfig:
    project_id = resolve_cli_or_env(cli_value=args.project_id, env_key="PLING_PROJECT_ID")
    if not project_id:
        raise PlingUploaderError("Missing project id. Set --project-id or env PLING_PROJECT_ID.")

    username = resolve_cli_or_env(cli_value=args.username, env_key="PLING_USERNAME")
    password = resolve_cli_or_env(cli_value=args.password, env_key="PLING_PASSWORD")
    if not username or not password:
        raise PlingUploaderError(
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
        raise PlingUploaderError("--max-retries must be >= 0")
    if timeout <= 0:
        raise PlingUploaderError("--timeout must be > 0")

    base_url = normalize_base_url(
        resolve_cli_or_env(
            cli_value=args.base_url,
            env_key="PLING_BASE_URL",
            default=DEFAULT_BASE_URL,
        )
        or DEFAULT_BASE_URL
    )

    artifact_path: Optional[Path] = None
    if not args.dry_run:
        artifact = resolve_cli_or_env(cli_value=args.artifact, env_key="PLING_ARTIFACT")
        if not artifact:
            raise PlingUploaderError(
                "Missing artifact path for upload mode. Set --artifact or env PLING_ARTIFACT."
            )
        artifact_path = Path(artifact)
        if not artifact_path.exists() or not artifact_path.is_file():
            raise PlingUploaderError(f"Artifact does not exist or is not a file: {artifact_path}")

    return RuntimeConfig(
        project_id=project_id,
        base_url=base_url,
        username=username,
        password=password,
        timeout=timeout,
        max_retries=max_retries,
        dry_run=args.dry_run,
        artifact_path=artifact_path,
    )


def create_session(base_url: str) -> "niquests.Session":
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
    session.cookies.set("verified", "1", domain=urlparse(base_url).hostname)
    return session


def discover_edit_context(
    session: "niquests.Session",
    config: RuntimeConfig,
) -> tuple[str, EditContext]:
    login_url = urljoin(config.base_url + "/", LOGIN_PATH.lstrip("/"))
    edit_url = urljoin(
        config.base_url + "/",
        EDIT_PATH_TEMPLATE.format(project_id=config.project_id).lstrip("/"),
    )

    log_event(
        "info",
        "session_start",
        base_url=config.base_url,
        project_id=config.project_id,
        dry_run=config.dry_run,
    )

    login_page = request_with_retries(
        session,
        "GET",
        login_url,
        timeout=config.timeout,
        max_retries=config.max_retries,
    )
    log_event("info", "login_page_fetched", **_safe_response_context(login_page))

    login_form = parse_login_form(login_page.text, login_url)
    form_payload = dict(login_form.hidden_fields)
    form_payload.update({"email": config.username, "password": config.password})

    auth_response = request_with_retries(
        session,
        "POST",
        login_form.action_url,
        data=form_payload,
        timeout=config.timeout,
        max_retries=config.max_retries,
    )
    log_event("info", "login_submitted", **_safe_response_context(auth_response))

    if "incorrect login" in auth_response.text.lower():
        raise PlingUploaderError("Authentication failed: incorrect username or password.")

    edit_page = request_with_retries(
        session,
        "GET",
        edit_url,
        timeout=config.timeout,
        max_retries=config.max_retries,
    )
    log_event("info", "edit_page_fetched", **_safe_response_context(edit_page))

    if looks_like_login_page(edit_page.text):
        raise PlingUploaderError(
            "Authentication appears unsuccessful: redirected back to login page."
        )

    if str(edit_page.url).rstrip("/") == normalize_base_url(login_url).rstrip("/"):
        raise PlingUploaderError("Unable to access edit page; received login page URL.")

    attrs = extract_required_data_attrs(edit_page.text, project_id=config.project_id)

    resolved: dict[str, str] = {}
    for key, value in attrs.items():
        replaced = value.replace("@@project_id@@", config.project_id)
        if key.endswith("-uri"):
            resolved[key] = urljoin(config.base_url + "/", replaced.lstrip("/"))
        else:
            resolved[key] = replaced

    client_id = extract_js_string_var(edit_page.text, "client_id")
    file_uri = extract_js_string_var(edit_page.text, "fileUri")
    owner_id = extract_form_append_literal(edit_page.text, "owner_id")

    missing_runtime = []
    if not client_id:
        missing_runtime.append("client_id")
    if not file_uri:
        missing_runtime.append("fileUri")
    if not owner_id:
        missing_runtime.append("owner_id")

    if missing_runtime:
        raise PlingUploaderError(
            "Edit page is missing required upload runtime values: "
            + ", ".join(missing_runtime)
        )

    context = EditContext(
        add_file_url=resolved["data-addpploadfile-uri"],
        update_file_url=resolved["data-updatepploadfile-uri"],
        delete_file_url=resolved["data-deletepploadfile-uri"],
        delete_all_files_url=resolved["data-deletepploadfiles-uri"],
        product_id=resolved["data-product-id"],
        collection_id=resolved["data-ppload-collection-id"],
        file_server_upload_url=file_uri,
        file_server_client_id=client_id,
        file_server_owner_id=owner_id,
    )

    log_event("info", "endpoint_discovered", name="data-addpploadfile-uri", value=context.add_file_url)
    log_event(
        "info",
        "endpoint_discovered",
        name="data-deletepploadfiles-uri",
        value=context.delete_all_files_url,
    )
    log_event(
        "info",
        "endpoint_discovered",
        name="data-ppload-collection-id",
        value=context.collection_id,
    )
    log_event("info", "endpoint_discovered", name="data-product-id", value=context.product_id)

    return edit_url, context


def delete_all_existing_files(
    session: "niquests.Session",
    config: RuntimeConfig,
    edit_url: str,
    context: EditContext,
) -> None:
    log_event(
        "warning",
        "delete_all_existing_files_start",
        message="Overwrite mode enabled: deleting all existing product files before upload.",
    )
    response = request_with_retries(
        session,
        "POST",
        context.delete_all_files_url,
        data={},
        headers={
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json",
            "Referer": edit_url,
        },
        timeout=config.timeout,
        max_retries=config.max_retries,
    )
    payload = parse_json_response(response, context="deletepploadfiles")
    status = payload.get("status")
    if status != "ok":
        raise PlingUploaderError(
            f"deletepploadfiles failed: status={status!r}"
        )
    log_event("info", "delete_all_existing_files_success")


def upload_to_file_server(
    session: "niquests.Session",
    config: RuntimeConfig,
    context: EditContext,
) -> dict[str, object]:
    if config.artifact_path is None:
        raise PlingUploaderError("Internal error: artifact path missing in upload mode.")

    log_event("info", "file_server_upload_start", artifact=str(config.artifact_path))

    with config.artifact_path.open("rb") as artifact_file:
        response = request_with_retries(
            session,
            "POST",
            context.file_server_upload_url,
            data={
                "client_id": context.file_server_client_id,
                "owner_id": context.file_server_owner_id,
                "format": "json",
                "collection_id": context.collection_id,
                "method": "post",
            },
            files={
                "file": (config.artifact_path.name, artifact_file),
            },
            timeout=max(config.timeout, 120.0),
            max_retries=config.max_retries,
        )

    payload = parse_json_response(response, context="file server upload")
    status = payload.get("status")
    if status != "success":
        raise PlingUploaderError(f"File server upload failed: status={status!r}")

    file_payload = payload.get("file")
    if not isinstance(file_payload, dict) or not file_payload:
        raise PlingUploaderError("File server upload succeeded but no file payload was returned.")

    log_event(
        "info",
        "file_server_upload_success",
        file_id=file_payload.get("id", "unknown"),
        file_name=file_payload.get("name", "unknown"),
    )
    return file_payload


def register_uploaded_file(
    session: "niquests.Session",
    config: RuntimeConfig,
    edit_url: str,
    context: EditContext,
    file_payload: dict[str, object],
) -> dict[str, object]:
    response = request_with_retries(
        session,
        "POST",
        context.add_file_url,
        data=file_payload,
        headers={
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json",
            "Referer": edit_url,
        },
        timeout=config.timeout,
        max_retries=config.max_retries,
    )

    payload = parse_json_response(response, context="addpploadfile")
    status = payload.get("status")
    if status != "ok":
        raise PlingUploaderError(f"addpploadfile failed: status={status!r}")

    registered_file = payload.get("file")
    if not isinstance(registered_file, dict) or not registered_file:
        raise PlingUploaderError("addpploadfile succeeded but no file payload was returned.")

    return registered_file


def run_dry_run(config: RuntimeConfig) -> int:
    session = create_session(config.base_url)
    _edit_url, _context = discover_edit_context(session, config)
    log_event(
        "info",
        "dry_run_success",
        message="Dry-run completed. No upload/update/delete actions were performed.",
    )
    return 0


def run_upload_mode(config: RuntimeConfig) -> int:
    session = create_session(config.base_url)
    edit_url, context = discover_edit_context(session, config)

    delete_all_existing_files(session, config, edit_url, context)
    file_payload = upload_to_file_server(session, config, context)
    registered_file = register_uploaded_file(session, config, edit_url, context, file_payload)

    log_event(
        "info",
        "upload_success",
        uploaded_file_id=registered_file.get("id", "unknown"),
        uploaded_file_name=registered_file.get("name", "unknown"),
    )
    return 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        config = resolve_runtime_config(args)
        if config.dry_run:
            return run_dry_run(config)
        return run_upload_mode(config)
    except PlingUploaderError as exc:
        log_event("error", "upload_failed", message=str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
