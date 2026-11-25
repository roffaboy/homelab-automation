#!/usr/bin/python
"""Ansible module: make one item of an app's REST API list look the way you describe.

How it works
------------
Many apps keep a group of items (Radarr connections, rules, notification settings) as a list at
one address: read the list with GET, add an item with POST, change one with PUT at its own address.
This module does that read-compare-write for one item:
  1. reads the list (GET) and finds the item whose O(match_key) equals the one in O(wanted);
  2. puts the O(wanted) keys on a copy of it;
  3. when there is no such item, creates it (POST); when the copy differs, saves it (PUT at
     <url>/<id>); when nothing differs it sends nothing, so a second run reports "ok";
  4. with O(state=absent) it deletes the item instead (DELETE at <url>/<id>).
Keys you do not name are kept as the app has them. The app is the one that is brought in line with
what you wrote: a value changed in the app's own screen is put back by the next run.

Supports check mode. The diff it returns hides the value of any key with "pass", "key", "token",
"secret" or "api" in its name. A value the app itself hides as "********" is never a difference.
"""

DOCUMENTATION = r"""
module: api_resource
short_description: Make one item of an app's REST API list match the given settings
description:
  - Finds an item in a JSON list by O(match_key), creates it when it is missing, saves it when the
    wanted keys differ, and does nothing when they match. For apps without an Ansible module.
options:
  url:
    description: Address of the list. It is read with GET and new items are POSTed to it.
    type: str
    required: true
  headers:
    description: Request headers, for example the API key or a bearer token. Treated as secret.
    type: dict
    default: {}
  list_key:
    description: Key holding the list when the app answers with an object (for example C(clients)).
    type: str
  match_key:
    description: Key that identifies the item in the list.
    type: str
    default: name
  wanted:
    description: Keys to set. Must include O(match_key) when O(state=present).
    type: dict
    default: {}
  create_url:
    description: Address new items are POSTed to, when it is not O(url).
    type: str
  recursive:
    description: Merge nested objects key by key instead of replacing them whole (lists are always replaced).
    type: bool
    default: false
  partial_update:
    description: Save only the wanted keys, not the whole item, for apps that take a partial change.
    type: bool
    default: false
  id_key:
    description: Key holding the item's id, used for its own address.
    type: str
    default: id
  item_url:
    description: Address of one item. C({url}) and C({id}) are replaced. Default C({url}/{id}).
    type: str
  update_method:
    description: HTTP method that saves a changed item.
    type: str
    choices: [PUT, PATCH, POST]
    default: PUT
  read_only:
    description: Keys that are not sent back, because the app refuses them.
    type: list
    elements: str
    default: []
  response_check:
    description:
      - Keys and values that the answer to a create, save or delete must contain, for apps that
        answer C(201) with C({status: NOK}) when they refuse. Anything else fails the module.
    type: dict
    default: {}
  state:
    description: C(absent) deletes the item.
    type: str
    choices: [present, absent]
    default: present
"""

EXAMPLES = r"""
- name: Maintainerr's Radarr connection
  api_resource:
    url: http://127.0.0.1:6246/api/settings/radarr
    match_key: serverName
    wanted:
      serverName: Radarr
      url: http://radarr:7878
      apiKey: "{{ radarr_key }}"
    response_check: {status: OK}

- name: Cleanuparr's Radarr instance
  api_resource:
    url: http://127.0.0.1:11011/api/configuration/radarr
    headers: {Authorization: "Bearer {{ token }}"}
    list_key: instances
    item_url: "{url}/instances/{id}"
    wanted: {name: Radarr, url: http://radarr:7878, apiKey: "{{ radarr_key }}"}
"""

RETURN = r"""
id:
  description: The item's id (not for a new item that is only checked).
  type: raw
  returned: when the item exists
diff:
  description: The keys that change, before and after, with secrets hidden.
  type: dict
  returned: when changed
"""

import json
import re
from copy import deepcopy
from urllib.error import HTTPError

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.urls import open_url

HIDDEN = "********"
MASK = re.compile(r"[*\u2022\u25cf\u00b7]{4,}")   # how apps show a secret they will not give back: ****, bullets


# --- Pure helpers (no API calls) ---------------------------------------------------------

def is_secret(key):
    """Keys whose values are hidden in the diff (passwords, API keys and tokens, secrets)."""
    return any(word in str(key).lower() for word in ("pass", "key", "token", "secret", "api"))


def items_of(answer, list_key):
    """The list in the app's answer: the answer itself, or the one under list_key."""
    items = answer.get(list_key) if list_key and isinstance(answer, dict) else answer
    if not isinstance(items, list):
        raise ValueError("the answer holds no list" + (" under %r" % list_key if list_key else ""))
    return items


def find(items, match_key, value):
    """The item whose match_key equals value, or None."""
    for item in items:
        if isinstance(item, dict) and item.get(match_key) == value:
            return item
    return None


def target_of(current, wanted, recursive):
    """What the wanted keys should hold: the value itself, or (recursive) the item's nested object with it set."""
    out = {}
    for key, value in wanted.items():
        if recursive and isinstance(value, dict) and isinstance(current.get(key), dict):
            out[key] = merged(current[key], value)
        else:
            out[key] = value
    return out


def merged(current, wanted):
    """Copy of the nested object with the wanted keys set, going down into nested objects."""
    result = deepcopy(current)
    for key, value in wanted.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merged(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def masked(value):
    """True for a value the app shows as a mask, not as what is stored."""
    return isinstance(value, str) and MASK.fullmatch(value) is not None


def differing(current, wanted):
    """Keys of wanted whose value differs from the item's. A value the app hides is no difference."""
    return {k: v for k, v in wanted.items() if current.get(k) != v and not masked(current.get(k))}


def shown(key, value):
    """A value as the diff shows it."""
    return HIDDEN if is_secret(key) and value is not None else value


def build_diff(current, changed):
    """before/after of the changed keys, secrets hidden. current is {} for a new item."""
    return dict(before={k: shown(k, current.get(k)) for k in changed},
                after={k: shown(k, v) for k, v in changed.items()})


def without(item, read_only):
    """Copy of the item without the read-only keys."""
    return {k: v for k, v in item.items() if k not in read_only}


def response_problem(answer, check):
    """Why the answer does not hold what response_check asks, or None."""
    if not check:
        return None
    if not isinstance(answer, dict):
        return "the answer is not an object: %r" % (answer,)
    for key, value in check.items():
        if answer.get(key) != value:
            return "the answer has %s=%r, not %r" % (key, answer.get(key), value)
    return None


# --- API access -----------------------------------------------------------------------------

def request(module, method, url, headers, data=None):
    """One JSON request; any failure ends the module with a clear message."""
    try:
        response = open_url(url, method=method, headers=dict(headers, **{"Content-Type": "application/json"}),
                            timeout=60, data=None if data is None else json.dumps(data))
        raw = response.read()
        try:
            return json.loads(raw) if raw else None
        except ValueError:
            return None   # an answer that is no JSON (plain text): only a read needs one
    except HTTPError as e:
        module.fail_json(msg="%s %s failed: HTTP %s" % (method, url, e.code), response=e.read().decode(errors="replace"))
    except Exception as e:  # connection refused, timeout, ...
        module.fail_json(msg="%s %s failed: %s" % (method, url, e))


# --- Module ---------------------------------------------------------------------------------

def main():
    module = AnsibleModule(
        argument_spec=dict(
            url=dict(type="str", required=True),
            headers=dict(type="dict", default={}, no_log=True),
            list_key=dict(type="str"),
            match_key=dict(type="str", default="name"),
            wanted=dict(type="dict", default={}),
            create_url=dict(type="str"),
            recursive=dict(type="bool", default=False),
            partial_update=dict(type="bool", default=False),
            id_key=dict(type="str", default="id"),
            item_url=dict(type="str"),
            update_method=dict(type="str", default="PUT", choices=["PUT", "PATCH", "POST"]),
            read_only=dict(type="list", elements="str", default=[]),
            response_check=dict(type="dict", default={}),
            state=dict(type="str", default="present", choices=["present", "absent"]),
        ),
        supports_check_mode=True,
    )
    p = module.params
    if p["match_key"] not in p["wanted"]:
        module.fail_json(msg="wanted must hold %r, the key that finds the item" % p["match_key"])

    answer = request(module, "GET", p["url"], p["headers"])
    try:
        items = items_of(answer, p["list_key"])
    except ValueError as e:
        module.fail_json(msg="GET %s: %s" % (p["url"], e))
    current = find(items, p["match_key"], p["wanted"][p["match_key"]])
    address = lambda item: (p["item_url"] or "{url}/{id}").format(url=p["url"], id=item[p["id_key"]])

    def checked(answer, what):
        problem = response_problem(answer, p["response_check"])
        if problem:
            module.fail_json(msg="%s %s: %s" % (what, p["url"], problem))

    if p["state"] == "absent":
        if current is None:
            module.exit_json(changed=False)
        if not module.check_mode:
            checked(request(module, "DELETE", address(current), p["headers"]), "DELETE")
        module.exit_json(changed=True, id=current.get(p["id_key"]),
                         diff=dict(before=build_diff(current, current)["before"], after={}))

    if current is None:
        if not module.check_mode:
            checked(request(module, "POST", p["create_url"] or p["url"], p["headers"], without(p["wanted"], p["read_only"])), "POST")
        module.exit_json(changed=True, diff=build_diff({}, p["wanted"]))

    target = target_of(current, p["wanted"], p["recursive"])
    changed = differing(current, target)
    if not changed:
        module.exit_json(changed=False, id=current.get(p["id_key"]))
    if not module.check_mode:
        body = without(target if p["partial_update"] else dict(deepcopy(current), **target), p["read_only"])
        checked(request(module, p["update_method"], address(current), p["headers"], body), p["update_method"])
    module.exit_json(changed=True, id=current.get(p["id_key"]), diff=build_diff(current, changed))


if __name__ == "__main__":
    main()
