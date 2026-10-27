import base64
import ctypes
import io
import json
import os
from pathlib import Path
import sys
import time
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".github/scripts"))
from smoke_plugin import Buffer, Plugin

DEFAULT_LIBRARY = ROOT / "target/release" / ("gflow.dll" if os.name == "nt" else "libgflow.dylib" if sys.platform == "darwin" else "libgflow.so")
LIBRARY = Path(os.environ.get("GFLOW_NATIVE_LIBRARY", str(DEFAULT_LIBRARY)))


@unittest.skipUnless(LIBRARY.is_file(), "Requires the library built by the native CI job")
class BootstrapTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data_dir = ROOT / ".build" / ("native-boundary-" + str(time.time_ns()))
        cls.library = ctypes.CDLL(str(LIBRARY.resolve()))
        cls.api = Plugin()
        init = cls.library.cliproxy_plugin_init
        init.argtypes = [ctypes.c_void_p, ctypes.POINTER(Plugin)]
        init.restype = ctypes.c_int
        if init(None, ctypes.byref(cls.api)):
            raise RuntimeError("Plugin init failed")
        config = "data_dir: " + json.dumps(str(cls.data_dir)) + "\nmanagement_key: synthetic-management-key\n"
        cls.metadata = cls.rpc("plugin.register", {"config_yaml": base64.b64encode(config.encode()).decode()})["result"]
        cls.registration = cls.rpc("management.register", {})["result"]
        assert not cls.data_dir.exists(), "Static registration must not start or deploy a worker"

    @classmethod
    def tearDownClass(cls):
        cls.api.shutdown()

    @classmethod
    def rpc(cls, method, payload):
        raw = json.dumps(payload).encode()
        request = ctypes.create_string_buffer(raw)
        response = Buffer()
        status = cls.api.call(method.encode(), request, len(raw), ctypes.byref(response))
        try:
            result = json.loads(ctypes.string_at(response.ptr, response.len))
        finally:
            cls.api.free_buffer(response.ptr, response.len)
        if status:
            raise RuntimeError(result.get("error", {}).get("message", "Plugin call failed"))
        return result

    def request(self, path, method="GET", query=None, body=None):
        request = {"Method": method, "Path": path, "Query": query or {}}
        if body is not None:
            request["Body"] = base64.b64encode(json.dumps(body).encode()).decode()
        result = self.rpc("management.handle", request)["result"]
        result["decoded"] = base64.b64decode(result["Body"])
        return result

    def test_public_html_contains_no_credentials(self):
        result = self.request("/v0/resource/plugins/gflow/manage")
        self.assertEqual(result["StatusCode"], 200)
        self.assertNotIn(b"synthetic-management-key", result["decoded"])
        self.assertIn(b"cli-proxy-auth", result["decoded"])
        self.assertNotIn(b"/gflow/bootstrap", result["decoded"])

    def test_resources_are_static_and_menu_has_no_ticket(self):
        resources = self.registration["Resources"]
        self.assertEqual({item["Path"] for item in resources}, {"/manage", "/extension.zip"})
        menu = next(item["Path"] for item in resources if item.get("Menu") == "GFlow")
        self.assertEqual(menu, "/manage")
        fields = self.metadata["metadata"]["ConfigFields"]
        self.assertEqual({item["Name"] for item in fields}, {"data_dir"})

    def test_public_dynamic_routes_are_unconditionally_removed(self):
        for suffix in ("bootstrap", "tmp", "tmp/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.jpg", "api/status", "manage/../api/status"):
            result = self.request("/v0/resource/plugins/gflow/" + suffix)
            self.assertEqual(result["StatusCode"], 404)
            self.assertNotIn(b"synthetic-management-key", result["decoded"])

    def test_public_resources_reject_mutating_methods(self):
        for suffix in ("manage", "extension.zip"):
            self.assertEqual(self.request("/v0/resource/plugins/gflow/" + suffix, "POST")["StatusCode"], 404)

    def test_extension_download_is_only_a_static_archive(self):
        result = self.request("/v0/resource/plugins/gflow/extension.zip")
        self.assertEqual(result["StatusCode"], 200)
        with zipfile.ZipFile(io.BytesIO(result["decoded"])) as archive:
            self.assertEqual(json.loads(archive.read("manifest.json"))["name"], "GFlow Login Assistant")
            self.assertIn("background.js", archive.namelist())

    def test_media_requires_management_route_and_stays_private(self):
        cache = self.data_dir / "tmp"
        cache.mkdir(parents=True, exist_ok=True)
        filename = "a" * 32 + ".mp4"
        (cache / filename).write_bytes(b"synthetic-video")
        result = self.request("/v0/management/plugins/gflow/api/media", query={"file": [filename]})
        self.assertEqual(result["StatusCode"], 200)
        self.assertEqual(result["decoded"], b"synthetic-video")
        self.assertIn("no-store", result["Headers"]["cache-control"][0])
        for invalid in ("../flow.db", "control-secret.json", "../" + filename):
            self.assertEqual(self.request("/v0/management/plugins/gflow/api/media", query={"file": [invalid]})["StatusCode"], 404)

    def test_control_executor_uses_standard_messages_without_management_key(self):
        body = {"model": "gflow-project-control", "stream": False, "messages": [{
            "role": "user", "content": json.dumps({"version": 1, "op": "capabilities",
                "workspaceId": "11111111-1111-4111-8111-111111111111"})}]}
        receipt = self.rpc("executor.execute", {"Model": "gflow-project-control",
            "Payload": base64.b64encode(json.dumps(body).encode()).decode()})["result"]
        payload = json.loads(base64.b64decode(receipt["Payload"]))
        control = json.loads(payload["choices"][0]["message"]["content"])
        self.assertTrue(control["projectBinding"])
        self.assertEqual(len(control["models"]), 7)
        self.assertNotIn("synthetic-management-key", json.dumps(control))

    def test_management_method_is_checked_before_starting_backend(self):
        self.assertEqual(self.request("/v0/management/plugins/gflow/api/status", "POST")["StatusCode"], 405)
        self.assertEqual(self.request("/v0/management/plugins/gflow/api/config/delete")["StatusCode"], 404)

    def test_sync_validation_preserves_http_failure(self):
        result = self.request("/v0/management/plugins/gflow/api/extension-sync", "POST", body={"login_account": "invalid"})
        self.assertEqual(result["StatusCode"], 400)
        body = json.loads(result["decoded"])
        self.assertFalse(body["success"])
        self.assertEqual(body["error"]["code"], "gflow_invalid_email")
        self.assertNotIn("traceback", str(body))


if __name__ == "__main__":
    unittest.main()
