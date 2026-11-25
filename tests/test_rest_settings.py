"""Tests of library/rest_settings.py against a small local HTTP server.

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

MODULE = os.path.join(os.path.dirname(__file__), "..", "library", "rest_settings.py")
sys.path.insert(0, os.path.dirname(MODULE))
import rest_settings  # noqa: E402


class App(BaseHTTPRequestHandler):
    """Serves one settings object at /settings and records what is written to it."""
    state = {}
    writes = []
    fail = False
    plain_reply = False

    def log_message(self, *args):
        pass

    def reply(self, code, body):
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        self.reply(500, {}) if App.fail else self.reply(200, App.state)

    def do_write(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        App.writes.append((self.command, self.headers.get("X-Api-Key"), body))
        App.state = body
        if App.plain_reply:   # some apps answer a save with plain text, or nothing
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"OK")
        else:
            self.reply(200, body)

    do_POST = do_PUT = do_PATCH = do_write


class Helpers(unittest.TestCase):
    def test_merge_replaces_top_level_values_and_keeps_the_rest(self):
        got = rest_settings.merged({"a": 1, "b": {"x": 1, "y": 2}}, {"b": {"x": 9}}, recursive=False)
        self.assertEqual(got, {"a": 1, "b": {"x": 9}})

    def test_recursive_merge_keeps_unnamed_nested_keys(self):
        got = rest_settings.merged({"a": 1, "b": {"x": 1, "y": 2}}, {"b": {"x": 9}}, recursive=True)
        self.assertEqual(got, {"a": 1, "b": {"x": 9, "y": 2}})

    def test_lists_are_replaced_whole_even_when_recursive(self):
        got = rest_settings.merged({"types": [1, 2, 3]}, {"types": [8]}, recursive=True)
        self.assertEqual(got, {"types": [8]})

    def test_merge_does_not_change_its_input(self):
        current = {"b": {"x": 1}}
        rest_settings.merged(current, {"b": {"x": 2}}, recursive=True)
        self.assertEqual(current, {"b": {"x": 1}})

    def test_the_diff_hides_secrets_and_keeps_the_rest(self):
        got = rest_settings.changes({"options": {"botAPI": "old", "chatId": "1", "token": "t"}, "enabled": False},
                                    {"options": {"botAPI": "new", "chatId": "2", "token": "t2"}, "enabled": True})
        self.assertEqual(got["enabled"], (False, True))
        self.assertEqual(got["options.chatId"], ("1", "2"))
        self.assertEqual(got["options.token"], (rest_settings.HIDDEN, rest_settings.HIDDEN))
        self.assertEqual(got["options.botAPI"], (rest_settings.HIDDEN, rest_settings.HIDDEN))
        self.assertNotIn("old", json.dumps(got))
        self.assertNotIn("new", json.dumps(got))


class Module(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), App)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = "http://127.0.0.1:%d/settings" % cls.server.server_port

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        App.state = {"enabled": False, "apiKey": "read-only", "options": {"chatId": "1", "botAPI": "x"}}
        App.writes = []
        App.fail = False
        App.plain_reply = False

    def run_module(self, **args):
        args.setdefault("url", self.url)
        args.setdefault("headers", {"X-Api-Key": "k"})
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"ANSIBLE_MODULE_ARGS": args}, f)
        try:
            out = subprocess.run([sys.executable, MODULE, f.name], capture_output=True, text=True).stdout
        finally:
            os.unlink(f.name)
        return json.loads(out)

    def test_a_difference_is_written_with_the_header_and_reported(self):
        result = self.run_module(wanted={"enabled": True})
        self.assertTrue(result["changed"])
        self.assertEqual(len(App.writes), 1)
        method, key, body = App.writes[0]
        self.assertEqual((method, key), ("POST", "k"))
        self.assertEqual(body["enabled"], True)
        self.assertEqual(body["options"], {"chatId": "1", "botAPI": "x"})   # untouched keys stay

    def test_the_second_run_changes_nothing(self):
        self.run_module(wanted={"enabled": True})
        App.writes.clear()
        result = self.run_module(wanted={"enabled": True})
        self.assertFalse(result["changed"])
        self.assertEqual(App.writes, [])

    def test_check_mode_reports_but_writes_nothing(self):
        result = self.run_module(wanted={"enabled": True}, _ansible_check_mode=True)
        self.assertTrue(result["changed"])
        self.assertEqual(App.writes, [])

    def test_read_only_keys_are_not_sent_back(self):
        self.run_module(wanted={"enabled": True}, read_only=["apiKey"])
        self.assertNotIn("apiKey", App.writes[0][2])

    def test_the_object_can_be_saved_at_another_address(self):
        self.run_module(wanted={"enabled": True}, write_url=self.url + "/save")
        self.assertEqual(App.writes[0][0], "POST")

    def test_the_write_method_can_be_chosen(self):
        self.run_module(wanted={"enabled": True}, write_method="PUT")
        self.assertEqual(App.writes[0][0], "PUT")

    def test_recursive_keeps_the_other_nested_keys(self):
        self.run_module(wanted={"options": {"chatId": "2"}}, recursive=True)
        self.assertEqual(App.writes[0][2]["options"], {"chatId": "2", "botAPI": "x"})

    def test_without_recursive_a_nested_object_is_replaced(self):
        self.run_module(wanted={"options": {"chatId": "2"}})
        self.assertEqual(App.writes[0][2]["options"], {"chatId": "2"})

    def test_the_result_never_contains_a_secret_value(self):
        App.state["options"]["botAPI"] = "old-bot-token"
        result = self.run_module(wanted={"options": {"botAPI": "new-bot-token"}}, recursive=True)
        self.assertTrue(result["changed"])
        self.assertNotIn("old-bot-token", json.dumps(result))
        self.assertNotIn("new-bot-token", json.dumps(result))

    def test_an_answer_to_a_save_that_is_not_json_is_fine(self):
        App.plain_reply = True
        result = self.run_module(wanted={"enabled": True})
        self.assertTrue(result["changed"])
        self.assertEqual(len(App.writes), 1)

    def test_an_app_error_fails_the_module_with_the_address(self):
        App.fail = True
        result = self.run_module(wanted={"enabled": True})
        self.assertTrue(result["failed"])
        self.assertIn("HTTP 500", result["msg"])
        self.assertIn(self.url, result["msg"])


if __name__ == "__main__":
    unittest.main()
