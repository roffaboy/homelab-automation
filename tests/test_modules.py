"""Tests of library/rest_settings.py and library/api_resource.py against a small local HTTP server.

The server holds one settings object (/settings) and one list of items (/items, or /wrapped with
the list under "items") and records every write. Run: python3 -m unittest discover -s tests
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

LIBRARY = os.path.join(os.path.dirname(__file__), "..", "library")


class App(BaseHTTPRequestHandler):
    settings, items, writes = {}, [], []
    mode = ""   # "fail": GET answers 500; "refuse": saves answer {"status": "NOK"}; "plain": saves answer text

    def log_message(self, *args):
        pass

    def send_json(self, code, body, raw=None):
        raw = raw or json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if App.mode == "fail":
            return self.send_json(500, {})
        self.send_json(200, {"items": App.items} if self.path == "/wrapped" else App.items if self.path == "/items" else App.settings)

    def do_write(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length)) if length else None
        App.writes.append((self.command, self.path, body, self.headers.get("X-Api-Key")))
        if self.command == "DELETE":
            App.items = [i for i in App.items if self.path != "/items/%s" % i["id"]]
        elif self.command == "POST" and self.path == "/items":
            App.items.append(dict(body, id=len(App.items) + 1))
        elif self.path.startswith("/settings"):
            App.settings = body
        if App.mode == "plain":
            return self.send_json(200, None, raw=b"saved")
        self.send_json(201, {"status": "NOK"} if App.mode == "refuse" else {"status": "OK"})

    do_POST = do_PUT = do_PATCH = do_DELETE = do_write


class ModuleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), App)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = "http://127.0.0.1:%d" % cls.server.server_port

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        App.settings = {"enabled": False, "apiKey": "read-only", "options": {"chatId": "1", "botAPI": "x"}}
        App.items = [{"id": 1, "name": "Radarr", "url": "http://radarr:7878", "apiKey": "********", "filters": {"a": 1, "b": 2}}]
        App.writes, App.mode = [], ""

    def run_module(self, module, path, **args):
        """The module's answer, run the way Ansible runs it."""
        args.update(url=self.base + path, headers={"X-Api-Key": "k"})
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as file:
            json.dump({"ANSIBLE_MODULE_ARGS": args}, file)
        try:
            return json.loads(subprocess.run([sys.executable, os.path.join(LIBRARY, module + ".py"), file.name],
                                             capture_output=True, text=True).stdout)
        finally:
            os.unlink(file.name)


class RestSettings(ModuleTest):
    def run_settings(self, **args):
        return self.run_module("rest_settings", "/settings", **args)

    def test_a_difference_is_saved_once_and_the_second_run_changes_nothing(self):
        self.assertTrue(self.run_settings(wanted={"enabled": True})["changed"])
        method, path, body, key = App.writes[0]
        self.assertEqual((method, key, body["enabled"], body["options"]), ("POST", "k", True, {"chatId": "1", "botAPI": "x"}))
        App.writes.clear()
        self.assertFalse(self.run_settings(wanted={"enabled": True})["changed"])
        self.assertEqual(App.writes, [])

    def test_check_mode_reports_but_saves_nothing(self):
        self.assertTrue(self.run_settings(wanted={"enabled": True}, _ansible_check_mode=True)["changed"])
        self.assertEqual(App.writes, [])

    def test_read_only_keys_stay_out_and_the_save_can_go_elsewhere(self):
        self.run_settings(wanted={"enabled": True}, read_only=["apiKey"], write_method="PUT", write_url=self.base + "/settings/save")
        method, path, body, _ = App.writes[0]
        self.assertEqual((method, path, "apiKey" in body), ("PUT", "/settings/save", False))

    def test_nested_objects_are_replaced_unless_recursive(self):
        self.run_settings(wanted={"options": {"chatId": "2"}})
        self.assertEqual(App.writes[0][2]["options"], {"chatId": "2"})
        App.settings = {"options": {"chatId": "1", "botAPI": "x"}}
        self.run_settings(wanted={"options": {"chatId": "2"}}, recursive=True)
        self.assertEqual(App.writes[1][2]["options"], {"chatId": "2", "botAPI": "x"})

    def test_an_answer_that_refuses_fails_and_one_that_is_no_json_does_not(self):
        App.mode = "refuse"
        result = self.run_settings(wanted={"enabled": True}, response_check={"status": "OK"})
        self.assertTrue(result["failed"])
        self.assertIn("NOK", result["msg"])
        App.mode, App.settings = "plain", {"enabled": False}
        self.assertTrue(self.run_settings(wanted={"enabled": True})["changed"])

    def test_an_app_error_names_the_address_and_no_secret_is_in_the_result(self):
        self.assertNotIn("new-bot-token", json.dumps(self.run_settings(wanted={"options": {"botAPI": "new-bot-token"}}, recursive=True)))
        App.mode = "fail"
        result = self.run_settings(wanted={"enabled": True})
        self.assertIn("HTTP 500", result["msg"])
        self.assertIn(self.base + "/settings", result["msg"])


class ApiResource(ModuleTest):
    def run_items(self, path="/items", **args):
        return self.run_module("api_resource", path, **args)

    def test_a_missing_item_is_created_and_a_different_one_is_saved_at_its_own_address(self):
        self.assertTrue(self.run_items(wanted={"name": "Sonarr", "url": "x"})["changed"])
        self.assertEqual(App.writes[0][:3], ("POST", "/items", {"name": "Sonarr", "url": "x"}))
        self.assertTrue(self.run_items(wanted={"name": "Radarr", "url": "http://radarr:9999"})["changed"])
        method, path, body, _ = App.writes[1]
        self.assertEqual((method, path, body["url"], body["filters"]), ("PUT", "/items/1", "http://radarr:9999", {"a": 1, "b": 2}))

    def test_the_second_run_changes_nothing_and_check_mode_saves_nothing(self):
        wanted = {"name": "Radarr", "url": "http://radarr:7878", "apiKey": "secret"}   # the app shows apiKey as a mask
        self.assertFalse(self.run_items(wanted=wanted)["changed"])
        self.assertTrue(self.run_items(wanted={"name": "Radarr", "url": "x"}, _ansible_check_mode=True)["changed"])
        self.assertEqual(App.writes, [])

    def test_a_mask_of_stars_or_bullets_is_no_difference_but_other_text_is(self):
        for mask, changed in (("****", False), ("•" * 8, False), ("***x", True)):
            App.items[0]["password"] = mask
            self.assertEqual(self.run_items(wanted={"name": "Radarr", "password": "real"})["changed"], changed, mask)

    def test_the_list_can_sit_under_a_key_and_the_addresses_can_be_chosen(self):
        self.run_items("/wrapped", list_key="items", item_url="{url}/instances/{id}", wanted={"name": "Radarr", "url": "x"})
        self.run_items(wanted={"name": "New"}, create_url=self.base + "/items/new")
        self.assertEqual([w[:2] for w in App.writes], [("PUT", "/wrapped/instances/1"), ("POST", "/items/new")])

    def test_nested_objects_are_replaced_unless_recursive_and_a_partial_save_sends_only_what_is_wanted(self):
        self.run_items(wanted={"name": "Radarr", "filters": {"a": 9}})
        self.assertEqual(App.writes[0][2]["filters"], {"a": 9})
        self.run_items(wanted={"name": "Radarr", "filters": {"a": 9}}, recursive=True, partial_update=True)
        self.assertEqual(App.writes[1][2], {"name": "Radarr", "filters": {"a": 9, "b": 2}})

    def test_read_only_keys_stay_out_and_absent_deletes(self):
        self.run_items(wanted={"name": "Radarr", "url": "x"}, read_only=["id", "filters"])
        self.assertTrue({"id", "filters"}.isdisjoint(App.writes[0][2]))
        self.assertTrue(self.run_items(wanted={"name": "Radarr"}, state="absent")["changed"])
        self.assertEqual(App.writes[1][:2], ("DELETE", "/items/1"))
        self.assertFalse(self.run_items(wanted={"name": "Radarr"}, state="absent")["changed"])

    def test_refusals_errors_and_missing_arguments_fail_clearly_and_no_secret_leaks(self):
        App.mode = "refuse"
        result = self.run_items(wanted={"name": "Sonarr", "apiKey": "super-secret-key"}, response_check={"status": "OK"})
        self.assertIn("NOK", result["msg"])
        self.assertNotIn("super-secret-key", json.dumps(result))
        self.assertTrue(self.run_items(wanted={"url": "x"})["failed"])   # without the key that finds the item
        App.mode = "fail"
        self.assertIn("HTTP 500", self.run_items(wanted={"name": "Radarr"})["msg"])


if __name__ == "__main__":
    unittest.main()
