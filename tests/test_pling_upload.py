import argparse
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "pling_upload.py"
SPEC = importlib.util.spec_from_file_location("pling_upload", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
MOD = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MOD
SPEC.loader.exec_module(MOD)


class DummyResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class PlingUploadTests(unittest.TestCase):
    def test_resolve_cli_or_env_prefers_cli(self):
        old = MOD.os.environ.get("PLING_PROJECT_ID")
        MOD.os.environ["PLING_PROJECT_ID"] = "from_env"
        try:
            value = MOD.resolve_cli_or_env(
                cli_value="from_cli",
                env_key="PLING_PROJECT_ID",
            )
            self.assertEqual(value, "from_cli")
        finally:
            if old is None:
                MOD.os.environ.pop("PLING_PROJECT_ID", None)
            else:
                MOD.os.environ["PLING_PROJECT_ID"] = old

    def test_extract_required_data_attrs_falls_back_product_id(self):
        html = (
            '<div data-addpploadfile-uri="/p/@@project_id@@/addpploadfile/" '
            'data-updatepploadfile-uri="/p/@@project_id@@/updatepploadfile/" '
            'data-deletepploadfile-uri="/p/@@project_id@@/deletepploadfile/" '
            'data-deletepploadfiles-uri="/p/@@project_id@@/deletepploadfiles/" '
            'data-product-id="" data-ppload-collection-id="123"></div>'
        )
        attrs = MOD.extract_required_data_attrs(html, project_id="42")
        self.assertEqual(attrs["data-product-id"], "42")

    def test_resolve_runtime_config_requires_files_in_upload_mode(self):
        old_env = dict(MOD.os.environ)
        try:
            MOD.os.environ["PLING_PROJECT_ID"] = "1"
            MOD.os.environ["PLING_USERNAME"] = "u"
            MOD.os.environ["PLING_PASSWORD"] = "p"
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
            with self.assertRaises(MOD.PlingUploaderError):
                MOD.resolve_runtime_config(args)
        finally:
            MOD.os.environ.clear()
            MOD.os.environ.update(old_env)

    def test_run_upload_mode_calls_steps_in_order(self):
        with tempfile.NamedTemporaryFile(suffix=".md") as tmp:
            config = MOD.RuntimeConfig(
                project_id="1",
                base_url="https://example.com",
                username="u",
                password="p",
                timeout=1.0,
                max_retries=0,
                dry_run=False,
                artifact_paths=[Path(tmp.name)],
            )
            context = MOD.EditContext(
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

            calls = []

            create_session_old = MOD.create_session
            discover_old = MOD.discover_edit_context
            delete_old = MOD.delete_all_existing_files
            upload_old = MOD.upload_to_file_server
            register_old = MOD.register_uploaded_file

            try:
                MOD.create_session = lambda _base: object()
                MOD.discover_edit_context = lambda _s, _c: ("https://example.com/edit", context)

                def fake_delete(_s, _cfg, _edit, _ctx):
                    calls.append("delete")

                def fake_upload(_s, _cfg, _ctx, _file):
                    calls.append("upload")
                    return {"id": "x"}

                def fake_register(_s, _cfg, _edit, _ctx, _file):
                    calls.append("register")
                    return {"id": "x", "name": "test.md"}

                MOD.delete_all_existing_files = fake_delete
                MOD.upload_to_file_server = fake_upload
                MOD.register_uploaded_file = fake_register

                rc = MOD.run_upload_mode(config)
                self.assertEqual(rc, 0)
                self.assertEqual(calls, ["delete", "upload", "register"])
            finally:
                MOD.create_session = create_session_old
                MOD.discover_edit_context = discover_old
                MOD.delete_all_existing_files = delete_old
                MOD.upload_to_file_server = upload_old
                MOD.register_uploaded_file = register_old

    def test_delete_all_existing_files_errors_on_non_ok(self):
        config = MOD.RuntimeConfig(
            project_id="1",
            base_url="https://example.com",
            username="u",
            password="p",
            timeout=1.0,
            max_retries=0,
            dry_run=False,
            artifact_paths=[Path("/tmp/does-not-matter")],
        )
        context = MOD.EditContext(
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

        old_request = MOD.request_with_retries
        try:
            MOD.request_with_retries = lambda *_a, **_kw: DummyResponse({"status": "error"})
            with self.assertRaises(MOD.PlingUploaderError):
                MOD.delete_all_existing_files(object(), config, "https://example.com/edit", context)
        finally:
            MOD.request_with_retries = old_request

    def test_run_upload_mode_with_multiple_files(self):
        with tempfile.NamedTemporaryFile(suffix=".md") as tmp1, tempfile.NamedTemporaryFile(
            suffix=".md"
        ) as tmp2:
            config = MOD.RuntimeConfig(
                project_id="1",
                base_url="https://example.com",
                username="u",
                password="p",
                timeout=1.0,
                max_retries=0,
                dry_run=False,
                artifact_paths=[Path(tmp1.name), Path(tmp2.name)],
            )
            context = MOD.EditContext(
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

            calls = []
            create_session_old = MOD.create_session
            discover_old = MOD.discover_edit_context
            delete_old = MOD.delete_all_existing_files
            upload_old = MOD.upload_to_file_server
            register_old = MOD.register_uploaded_file

            try:
                MOD.create_session = lambda _base: object()
                MOD.discover_edit_context = lambda _s, _c: ("https://example.com/edit", context)
                MOD.delete_all_existing_files = lambda *_a, **_kw: calls.append("delete")

                def fake_upload(_s, _cfg, _ctx, file_path):
                    calls.append(f"upload:{Path(file_path).name}")
                    return {"id": Path(file_path).name}

                def fake_register(_s, _cfg, _edit, _ctx, file_payload):
                    calls.append(f"register:{file_payload['id']}")
                    return {"id": file_payload["id"], "name": file_payload["id"]}

                MOD.upload_to_file_server = fake_upload
                MOD.register_uploaded_file = fake_register

                rc = MOD.run_upload_mode(config)
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
                MOD.create_session = create_session_old
                MOD.discover_edit_context = discover_old
                MOD.delete_all_existing_files = delete_old
                MOD.upload_to_file_server = upload_old
                MOD.register_uploaded_file = register_old


if __name__ == "__main__":
    unittest.main()
