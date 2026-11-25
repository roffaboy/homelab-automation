#!/usr/bin/python
"""Ansible module: make the settings object of an app's REST API look the way you describe.

How it works
------------
Many apps keep a group of settings as one JSON object at one address: read it with GET, change
what you want, send it back. This module does that read-compare-write for you:
  1. reads the object (GET);
  2. puts your O(wanted) keys on a copy of it;
  3. when the copy differs, sends it back (POST or PUT) and reports "changed"; when it does not,
     sends nothing, so a second run reports "ok".
Keys you do not name are kept as the app has them. Nested objects are merged key by key when
O(recursive) is true; lists and other values are replaced as a whole either way.

Supports check mode. The diff it returns hides the value of any key with "pass", "key", "token",
"secret" or "api" in its name.
"""

DOCUMENTATION = r"""
module: rest_settings
short_description: Set keys of a settings object that an app's REST API serves
description:
  - Reads a JSON settings object, sets the wanted keys and writes it back only when something
    differs. For apps that have no Ansible module and keep a group of settings at one address.
options:
  url:
    description: Address of the settings object. It is read with GET and written to the same address.
    type: str
    required: true
  write_url:
    description: Address the object is saved to, when it is not O(url) (some apps read and save at different addresses).
    type: str
  headers:
    description: Request headers, for example the API key. Treated as secret.
    type: dict
    default: {}
  wanted:
    description: Keys to set. Keys that are not named stay as the app has them.
    type: dict
    required: true
  recursive:
    description: Merge nested objects key by key instead of replacing them whole.
    type: bool
    default: false
  write_method:
    description: HTTP method that saves the object.
    type: str
    choices: [POST, PUT, PATCH]
    default: POST
  read_only:
    description: Keys that are not sent back, because the app refuses them (for example an API key it only shows).
    type: list
    elements: str
    default: []
"""

EXAMPLES = r"""
- name: Seerr's Telegram notifications
  rest_settings:
    url: http://127.0.0.1:5055/api/v1/settings/notifications/telegram
    headers: { X-Api-Key: "{{ seerr_api_key }}" }
    recursive: true
    wanted:
      enabled: true
      options: { botAPI: "{{ token }}", chatId: "{{ chat }}" }

- name: Seerr's defaults for every user
  rest_settings:
    url: http://127.0.0.1:5055/api/v1/settings/main
    headers: { X-Api-Key: "{{ seerr_api_key }}" }
    read_only: [apiKey]
    wanted: { originalLanguage: "nl|en", streamingRegion: NL }
"""

RETURN = r"""
diff:
  description: The keys that change, before and after, with secrets hidden.
  type: dict
  returned: when changed
"""

import json
from copy import deepcopy
from urllib.error import HTTPError

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.urls import open_url

HIDDEN = "********"


# --- Pure helpers (no API calls) ---------------------------------------------------------

def merged(current, wanted, recursive):
    """Copy of current with the wanted keys set. Nested objects merge only when recursive."""
    result = deepcopy(current)
    for key, value in wanted.items():
        if recursive and isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merged(result[key], value, recursive)
        else:
            result[key] = deepcopy(value)
    return result


def is_secret(key):
    """Keys whose values are hidden in the diff (passwords, API keys and tokens, secrets)."""
    return any(word in str(key).lower() for word in ("pass", "key", "token", "secret", "api"))


def changes(before, after, path=""):
    """Dotted key -> (before, after) for every value that differs, secrets hidden."""
    out = {}
    for key in sorted(set(before) | set(after), key=str):
        old, new = before.get(key), after.get(key)
        name = "%s%s" % (path, key)
        if old == new:
            continue
        if isinstance(old, dict) and isinstance(new, dict):
            out.update(changes(old, new, name + "."))
        elif is_secret(key):
            out[name] = (HIDDEN if old is not None else None, HIDDEN if new is not None else None)
        else:
            out[name] = (old, new)
    return out


def without(settings, read_only):
    """Copy of the settings without the read-only keys."""
    return {k: v for k, v in settings.items() if k not in read_only}


# --- API access -----------------------------------------------------------------------------

def request(module, method, url, headers, data=None):
    """One JSON request; any failure ends the module with a clear message."""
    try:
        response = open_url(url, method=method, headers=dict(headers, **{"Content-Type": "application/json"}),
                            timeout=60, data=None if data is None else json.dumps(data))
        raw = response.read()
        if method != "GET":
            return None   # what the app answers to a save is not needed, and need not be JSON
        return json.loads(raw) if raw else None
    except HTTPError as e:
        module.fail_json(msg="%s %s failed: HTTP %s" % (method, url, e.code), response=e.read().decode(errors="replace"))
    except Exception as e:  # connection refused, timeout, a body that is no JSON, ...
        module.fail_json(msg="%s %s failed: %s" % (method, url, e))


# --- Module ---------------------------------------------------------------------------------

def main():
    module = AnsibleModule(
        argument_spec=dict(
            url=dict(type="str", required=True),
            write_url=dict(type="str"),
            headers=dict(type="dict", default={}, no_log=True),
            wanted=dict(type="dict", required=True),
            recursive=dict(type="bool", default=False),
            write_method=dict(type="str", default="POST", choices=["POST", "PUT", "PATCH"]),
            read_only=dict(type="list", elements="str", default=[]),
        ),
        supports_check_mode=True,
    )
    p = module.params
    current = request(module, "GET", p["url"], p["headers"])
    if not isinstance(current, dict):
        module.fail_json(msg="GET %s did not return a JSON object" % p["url"])
    wanted = merged(current, p["wanted"], p["recursive"])
    if wanted == current:
        module.exit_json(changed=False)
    diff = changes(current, wanted)
    if not module.check_mode:
        request(module, p["write_method"], p["write_url"] or p["url"], p["headers"], without(wanted, p["read_only"]))
    module.exit_json(changed=True,
                     diff=dict(before={k: v[0] for k, v in diff.items()}, after={k: v[1] for k, v in diff.items()}))


if __name__ == "__main__":
    main()
