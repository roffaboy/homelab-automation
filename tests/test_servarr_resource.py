"""Tests of how library/servarr_resource.py compares an item with what is wanted."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "library"))
import servarr_resource  # noqa: E402


class Differences(unittest.TestCase):
    def test_a_login_with_the_same_user_is_no_difference_although_the_password_comes_back_as_a_hash(self):
        current = {"username": "me", "password": "k3J9hash+/=", "passwordConfirmation": "", "trustedNetworks": ""}
        wanted = {"username": "me", "password": "plain", "passwordConfirmation": "plain", "trustedNetworks": ""}
        self.assertEqual(servarr_resource.differences(current, wanted, {}), ({}, {}))

    def test_another_value_is_a_difference_and_the_secret_is_not(self):
        current = {"username": "me", "password": "hash", "trustedNetworks": "172.22.0.1"}
        wanted = {"username": "me", "password": "plain", "trustedNetworks": ""}
        self.assertEqual(servarr_resource.differences(current, wanted, {}), ({"trustedNetworks": "172.22.0.1"}, {"trustedNetworks": ""}))

    def test_a_hidden_field_is_skipped_and_another_field_is_compared(self):
        current = {"fields": [{"name": "apiKey", "value": "********"}, {"name": "host", "value": "a"}]}
        before, after = servarr_resource.differences(current, {}, {"apiKey": "k", "host": "b"})
        self.assertEqual((before, after), ({"fields.host": "a"}, {"fields.host": "b"}))


if __name__ == "__main__":
    unittest.main()
