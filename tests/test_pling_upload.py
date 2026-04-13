import argparse
import importlib.util
import os
import sys
import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import cast

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "pling_upload.py"
SPEC = importlib.util.spec_from_file_location("pling_upload", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
MOD = cast(ModuleType, importlib.util.module_from_spec(SPEC))
sys.modules[SPEC.name] = MOD
SPEC.loader.exec_module(MOD)

resolve_cli_or_env = cast(Callable[..., str | None], getattr(MOD, "resolve_cli_or_env"))
extract_required_data_attrs = cast(
    Callable[..., dict[str, str]],
    getattr(MOD, "extract_required_data_attrs"),
)
resolve_runtime_config = cast(Callable[[argparse.Namespace], object], getattr(MOD, "resolve_runtime_config"))
run_upload_mode = cast(Callable[[object], int], getattr(MOD, "run_upload_mode"))
delete_all_existing_files = cast(
    Callable[[object, object, str, object], None],
    getattr(MOD, "delete_all_existing_files"),
)
RuntimeConfig = cast(Callable[..., object], getattr(MOD, "RuntimeConfig"))
EditContext = cast(Callable[..., object], getattr(MOD, "EditContext"))
PlingUploaderError = cast(type[Exception], getattr(MOD, "PlingUploaderError"))


class DummyResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def json(self) -> dict[str, object]:
        return self._payload


class PlingUploadTests(unittest.TestCase):
    def test_resolve_cli_or_env_prefers_cli(self) -> None:
        old = os.environ.get("PLING_PROJECT_ID")
        os.environ["PLING_PROJECT_ID"] = "from_env"
        try:
            value = resolve_cli_or_env(
                cli_value="from_cli",
                env_key="PLING_PROJECT_ID",
            )
            self.assertEqual(value, "from_cli")
        finally:
            if old is None:
                os.environ.pop("PLING_PROJECT_ID", None)
            else:
                os.environ["PLING_PROJECT_ID"] = old

    def test_extract_required_data_attrs_falls_back_product_id(self) -> None:
        html = (
            '<div data-addpploadfile-uri="/p/@@project_id@@/addpploadfile/" '
            'data-updatepploadfile-uri="/p/@@project_id@@/updatepploadfile/" '
            'data-deletepploadfile-uri="/p/@@project_id@@/deletepploadfile/" '
            'data-deletepploadfiles-uri="/p/@@project_id@@/deletepploadfiles/" '
            'data-product-id="" data-ppload-collection-id="123"></div>'
        )
        attrs = extract_required_data_attrs(html, "42")
        self.assertEqual(attrs["data-product-id"], "42")

    def test_resolve_runtime_config_requires_files_in_upload_mode(self) -> None:
        old_env = dict(os.environ)
        try:
            os.environ["PLING_PROJECT_ID"] = "1"
            os.environ["PLING_USERNAME"] = "u"
            os.environ["PLING_PASSWORD"] = "p"
            args = argparse.Namespace(
                files=[],
                project_id=None,
                base_url=None,
                username=None,
                password=None,
                timeout=None,
                max_retries=None,
                dry_run=False,
            )
            with self.assertRaises(PlingUploaderError):
                resolve_runtime_config(args)
        finally:
            os.environ.clear()
            os.environ.update(old_env)

    def test_run_upload_mode_calls_steps_in_order(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".md") as tmp:
            config = RuntimeConfig(
                project_id="1",
                base_url="https://example.com",
                username="u",
                password="p",
                timeout=1.0,
                max_retries=0,
                dry_run=False,
                artifact_paths=[Path(tmp.name)],
            )
            context = EditContext(
                add_file_url="https://example.com/add",
                update_file_url="https://example.com/update",
                delete_file_url="https://example.com/delete",
                delete_all_files_url="https://example.com/delete-all",
                product_id="1",
                collection_id="2",
                file_server_upload_url="https://files.example/upload",
                file_server_client_id="3",
                file_server_owner_id="4",
            )

            calls: list[str] = []
            create_session_old = getattr(MOD, "create_session")
            discover_old = getattr(MOD, "discover_edit_context")
            delete_old = getattr(MOD, "delete_all_existing_files")
            upload_old = getattr(MOD, "upload_to_file_server")
            register_old = getattr(MOD, "register_uploaded_file")

            try:
                setattr(MOD, "create_session", lambda _base: object())
                setattr(MOD, "discover_edit_context", lambda _s, _c: ("https://example.com/edit", context))

                def fake_delete(_s: object, _cfg: object, _edit: str, _ctx: object) -> None:
                    calls.append("delete")

                def fake_upload(_s: object, _cfg: object, _ctx: object, _file: Path) -> dict[str, object]:
                    calls.append("upload")
                    return {"id": "x"}

                def fake_register(
                    _s: object,
                    _cfg: object,
                    _edit: str,
                    _ctx: object,
                    _file: dict[str, object],
                ) -> dict[str, object]:
                    calls.append("register")
                    return {"id": "x", "name": "test.md"}

                setattr(MOD, "delete_all_existing_files", fake_delete)
                setattr(MOD, "upload_to_file_server", fake_upload)
                setattr(MOD, "register_uploaded_file", fake_register)

                rc = run_upload_mode(config)
                self.assertEqual(rc, 0)
                self.assertEqual(calls, ["delete", "upload", "register"])
            finally:
                setattr(MOD, "create_session", create_session_old)
                setattr(MOD, "discover_edit_context", discover_old)
                setattr(MOD, "delete_all_existing_files", delete_old)
                setattr(MOD, "upload_to_file_server", upload_old)
                setattr(MOD, "register_uploaded_file", register_old)

    def test_delete_all_existing_files_errors_on_non_ok(self) -> None:
        config = RuntimeConfig(
            project_id="1",
            base_url="https://example.com",
            username="u",
            password="p",
            timeout=1.0,
            max_retries=0,
            dry_run=False,
            artifact_paths=[Path("/tmp/does-not-matter")],
        )
        context = EditContext(
            add_file_url="https://example.com/add",
            update_file_url="https://example.com/update",
            delete_file_url="https://example.com/delete",
            delete_all_files_url="https://example.com/delete-all",
            product_id="1",
            collection_id="2",
            file_server_upload_url="https://files.example/upload",
            file_server_client_id="3",
            file_server_owner_id="4",
        )

        old_request = getattr(MOD, "request_with_retries")
        try:
            setattr(MOD, "request_with_retries", lambda *_a, **_kw: DummyResponse({"status": "error"}))
            with self.assertRaises(PlingUploaderError):
                delete_all_existing_files(object(), config, "https://example.com/edit", context)
        finally:
            setattr(MOD, "request_with_retries", old_request)

    def test_run_upload_mode_with_multiple_files(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".md") as tmp1, tempfile.NamedTemporaryFile(
            suffix=".md"
        ) as tmp2:
            config = RuntimeConfig(
                project_id="1",
                base_url="https://example.com",
                username="u",
                password="p",
                timeout=1.0,
                max_retries=0,
                dry_run=False,
                artifact_paths=[Path(tmp1.name), Path(tmp2.name)],
            )
            context = EditContext(
                add_file_url="https://example.com/add",
                update_file_url="https://example.com/update",
                delete_file_url="https://example.com/delete",
                delete_all_files_url="https://example.com/delete-all",
                product_id="1",
                collection_id="2",
                file_server_upload_url="https://files.example/upload",
                file_server_client_id="3",
                file_server_owner_id="4",
            )

            calls: list[str] = []
            create_session_old = getattr(MOD, "create_session")
            discover_old = getattr(MOD, "discover_edit_context")
            delete_old = getattr(MOD, "delete_all_existing_files")
            upload_old = getattr(MOD, "upload_to_file_server")
            register_old = getattr(MOD, "register_uploaded_file")

            try:
                setattr(MOD, "create_session", lambda _base: object())
                setattr(MOD, "discover_edit_context", lambda _s, _c: ("https://example.com/edit", context))
                setattr(MOD, "delete_all_existing_files", lambda *_a, **_kw: calls.append("delete"))

                def fake_upload(_s: object, _cfg: object, _ctx: object, file_path: Path) -> dict[str, object]:
                    calls.append(f"upload:{Path(file_path).name}")
                    return {"id": Path(file_path).name}

                def fake_register(
                    _s: object,
                    _cfg: object,
                    _edit: str,
                    _ctx: object,
                    file_payload: dict[str, object],
                ) -> dict[str, object]:
                    file_id = str(file_payload["id"])
                    calls.append(f"register:{file_id}")
                    return {"id": file_id, "name": file_id}

                setattr(MOD, "upload_to_file_server", fake_upload)
                setattr(MOD, "register_uploaded_file", fake_register)

                rc = run_upload_mode(config)
                self.assertEqual(rc, 0)
                self.assertEqual(
                    calls,
                    [
                        "delete",
                        f"upload:{Path(tmp1.name).name}",
                        f"register:{Path(tmp1.name).name}",
                        f"upload:{Path(tmp2.name).name}",
                        f"register:{Path(tmp2.name).name}",
                    ],
                )
            finally:
                setattr(MOD, "create_session", create_session_old)
                setattr(MOD, "discover_edit_context", discover_old)
                setattr(MOD, "delete_all_existing_files", delete_old)
                setattr(MOD, "upload_to_file_server", upload_old)
                setattr(MOD, "register_uploaded_file", register_old)


if __name__ == "__main__":
    _ = unittest.main()
