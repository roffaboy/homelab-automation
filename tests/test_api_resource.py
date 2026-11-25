"""Tests of library/api_resource.py against a small local HTTP server.

Run: python3 -m unittest discover -s tests   (needs ansible-core, see requirements.txt)
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

MODULE = os.path.join(os.path.dirname(__file__), "..", "library", "api_resource.py")
sys.path.insert(0, os.path.dirname(MODULE))
import api_resource  # noqa: E402


class App(BaseHTTPRequestHandler):
    """Serves a list at /items (or {"items": [...]} at /wrapped) and records the writes."""
    items = []
    writes = []
    status = "OK"
    plain = False

    def log_message(self, *args):
        pass

    def reply(self, code, body):
        raw = b"fine" if App.plain and self.command != "GET" else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        self.reply(200, {"items": App.items} if self.path == "/wrapped" else App.items)

    def read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length)) if length else None

    def do_POST(self):
        body = self.read_body()
        App.writes.append(("POST", self.path, body))
        App.items.append(dict(body, id=len(App.items) + 1))
        self.reply(201, {"status": App.status})

    def do_PUT(self):
        body = self.read_body()
        App.writes.append(("PUT", self.path, body))
        self.reply(200, {"status": App.status})

    def do_DELETE(self):
        App.writes.append(("DELETE", self.path, None))
        App.items[:] = [i for i in App.items if "/%s" % i["id"] != self.path[self.path.rindex("/"):]]
        self.reply(200, {"status": App.status})


class Helpers(unittest.TestCase):
    def test_items_of_takes_the_list_itself_or_the_one_under_a_key(self):
        self.assertEqual(api_resource.items_of([1], None), [1])
        self.assertEqual(api_resource.items_of({"clients": [2]}, "clients"), [2])
        with self.assertRaises(ValueError):
            api_resource.items_of({"x": 1}, "clients")

    def test_find_matches_on_the_key(self):
        items = [{"name": "a", "id": 1}, {"name": "b", "id": 2}]
        self.assertEqual(api_resource.find(items, "name", "b")["id"], 2)
        self.assertIsNone(api_resource.find(items, "name", "c"))

    def test_a_value_the_app_hides_is_no_difference(self):
        got = api_resource.differing({"url": "x", "apiKey": "********"}, {"url": "x", "apiKey": "real"})
        self.assertEqual(got, {})

    def test_a_mask_of_stars_or_bullets_is_no_difference_but_other_text_is(self):
        for mask in ("********", "\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022", "****"):
            self.assertEqual(api_resource.differing({"password": mask}, {"password": "real"}), {})
        self.assertEqual(api_resource.differing({"password": "abc"}, {"password": "real"}), {"password": "real"})
        self.assertEqual(api_resource.differing({"password": "***x"}, {"password": "real"}), {"password": "real"})

    def test_the_diff_hides_secrets_only(self):
        got = api_resource.build_diff({"url": "a", "apiKey": "old"}, {"url": "b", "apiKey": "new"})
        self.assertEqual(got["before"], {"url": "a", "apiKey": api_resource.HIDDEN})
        self.assertEqual(got["after"], {"url": "b", "apiKey": api_resource.HIDDEN})

    def test_response_check(self):
        self.assertIsNone(api_resource.response_problem({"status": "OK"}, {"status": "OK"}))
        self.assertIn("NOK", api_resource.response_problem({"status": "NOK"}, {"status": "OK"}))
        self.assertIsNone(api_resource.response_problem(None, {}))


class Module(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), App)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = "http://127.0.0.1:%d" % cls.server.server_port

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        App.items = [{"id": 1, "name": "Radarr", "url": "http://radarr:7878", "apiKey": "********", "tag": True}]
        App.writes = []
        App.status = "OK"
        App.plain = False

    def run_module(self, path="/items", **args):
        args.setdefault("url", self.base + path)
        args.setdefault("headers", {"X-Api-Key": "k"})
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"ANSIBLE_MODULE_ARGS": args}, f)
        try:
            out = subprocess.run([sys.executable, MODULE, f.name], capture_output=True, text=True).stdout
        finally:
            os.unlink(f.name)
        return json.loads(out)

    def test_a_missing_item_is_created(self):
        result = self.run_module(wanted={"name": "Sonarr", "url": "http://sonarr:8989"})
        self.assertTrue(result["changed"])
        self.assertEqual(App.writes, [("POST", "/items", {"name": "Sonarr", "url": "http://sonarr:8989"})])

    def test_recursive_keeps_the_nested_keys_you_did_not_name(self):
        App.items[0]["filters"] = {"a": 1, "b": 2}
        result = self.run_module(wanted={"name": "Radarr", "filters": {"a": 9}}, recursive=True)
        self.assertTrue(result["changed"])
        self.assertEqual(App.writes[0][2]["filters"], {"a": 9, "b": 2})
        App.writes.clear()
        self.assertFalse(self.run_module(wanted={"name": "Radarr", "filters": {"a": 1}}, recursive=True)["changed"])

    def test_without_recursive_a_nested_object_is_replaced(self):
        App.items[0]["filters"] = {"a": 1, "b": 2}
        self.run_module(wanted={"name": "Radarr", "filters": {"a": 9}})
        self.assertEqual(App.writes[0][2]["filters"], {"a": 9})

    def test_a_partial_update_sends_only_the_wanted_keys(self):
        self.run_module(wanted={"name": "Radarr", "url": "x"}, partial_update=True)
        self.assertEqual(App.writes[0][2], {"name": "Radarr", "url": "x"})

    def test_a_new_item_can_be_posted_to_another_address(self):
        self.run_module(wanted={"name": "Sonarr"}, create_url=self.base + "/items/new")
        self.assertEqual(App.writes[0][:2], ("POST", "/items/new"))

    def test_a_different_value_is_saved_at_the_items_own_address(self):
        result = self.run_module(wanted={"name": "Radarr", "url": "http://radarr:9999"})
        self.assertTrue(result["changed"])
        method, path, body = App.writes[0]
        self.assertEqual((method, path), ("PUT", "/items/1"))
        self.assertEqual(body["url"], "http://radarr:9999")
        self.assertEqual(body["tag"], True)   # a key you did not name stays as the app has it

    def test_the_second_run_changes_nothing(self):
        result = self.run_module(wanted={"name": "Radarr", "url": "http://radarr:7878", "apiKey": "secret"})
        self.assertFalse(result["changed"])
        self.assertEqual(App.writes, [])

    def test_check_mode_reports_but_writes_nothing(self):
        result = self.run_module(wanted={"name": "Radarr", "url": "x"}, _ansible_check_mode=True)
        self.assertTrue(result["changed"])
        self.assertEqual(App.writes, [])

    def test_the_list_can_sit_under_a_key_and_the_item_address_can_be_chosen(self):
        self.run_module(path="/wrapped", list_key="items", item_url="{url}/instances/{id}",
                        wanted={"name": "Radarr", "url": "x"})
        self.assertEqual(App.writes[0][:2], ("PUT", "/wrapped/instances/1"))

    def test_read_only_keys_are_not_sent_back(self):
        self.run_module(wanted={"name": "Radarr", "url": "x"}, read_only=["id", "tag"])
        body = App.writes[0][2]
        self.assertNotIn("id", body)
        self.assertNotIn("tag", body)

    def test_absent_deletes_the_item(self):
        result = self.run_module(wanted={"name": "Radarr"}, state="absent")
        self.assertTrue(result["changed"])
        self.assertEqual(App.writes, [("DELETE", "/items/1", None)])
        self.assertEqual(self.run_module(wanted={"name": "Radarr"}, state="absent")["changed"], False)

    def test_an_answer_that_refuses_fails_the_module(self):
        App.status = "NOK"
        result = self.run_module(wanted={"name": "Sonarr"}, response_check={"status": "OK"})
        self.assertTrue(result["failed"])
        self.assertIn("NOK", result["msg"])

    def test_an_answer_that_is_no_json_is_fine_without_a_check(self):
        App.plain = True
        result = self.run_module(wanted={"name": "Radarr", "url": "x"})
        self.assertTrue(result["changed"])

    def test_the_result_never_contains_a_secret_value(self):
        result = self.run_module(wanted={"name": "Sonarr", "apiKey": "super-secret-key"})
        self.assertNotIn("super-secret-key", json.dumps(result))

    def test_the_match_key_must_be_in_wanted(self):
        result = self.run_module(wanted={"url": "x"})
        self.assertTrue(result["failed"])


if __name__ == "__main__":
    unittest.main()
