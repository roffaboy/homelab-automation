#!/usr/bin/env python3
"""Writes the settings of the running apps as one JSON file, without secrets, so two installs can be compared.

    tools/dump-config.py > live.json          # on the server
    tools/dump-config.py > fresh.json         # on a fresh install (CI makes one)
    diff <(python3 -m json.tool --sort-keys live.json) <(python3 -m json.tool --sort-keys fresh.json)

What differs is what the playbook does not set: something made by hand, left over from an earlier
version, or an app default that you changed. Media, users' watch history and requests are not settings
and are not in it. Read only: it only asks the apps for their settings.

It signs in with your login (the vault in config/), reads the apps' own API keys from their folders, and
needs the repo's venv (ansible-vault). Values that are secrets, ids and times are left out or
replaced, so what remains is the same on every install.
"""

import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRV = os.environ.get("HOMELAB_BASE_DIR", os.path.expanduser("~/srv"))
SECRET = re.compile(r"pass|key|token|secret|salt|hash|cookie|bearer|authorization", re.I)
VOLATILE = {"id", "added", "lastExecuted", "lastRun", "createdAt", "updatedAt", "created_at", "updated_at", "next_run", "stats",
            "lastStartTime", "lastEndTime", "lastRunResult", "nextExecution", "uptime", "version", "startTime", "UserId",
            "ServerId", "Id", "DateCreated", "DateLastActivity", "DateLastLogin", "LastLoginDate", "LastActivityDate",
            "jellyfin_user_id", "owner_id", "user_ids", "implementationName", "infoLink", "helpLink", "ipAddress"}


def clean(value, key=""):
    """Without secrets and without what differs on every install."""
    if isinstance(value, dict):
        return {k: clean(v, k) for k, v in sorted(value.items()) if k not in VOLATILE and not SECRET.search(k)}
    if isinstance(value, list):
        items = [clean(v) for v in value]
        return sorted(items, key=lambda x: json.dumps(x, sort_keys=True, default=str))
    return value


def get(url, headers=None, data=None, method=None):
    request = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    with urllib.request.urlopen(request, timeout=30) as response:
        raw = response.read()
        return json.loads(raw) if raw else None


def login_details():
    vault = subprocess.run([os.path.join(ROOT, ".venv/bin/ansible-vault"), "view", os.path.join(ROOT, "config/vault.yml")],
                           capture_output=True, text=True, cwd=ROOT, check=True).stdout
    password = re.search(r"^vault_homelab_password:\s*(.+)$", vault, re.M).group(1).strip().strip("\"'")
    settings = open(os.path.join(ROOT, "config/settings.yml")).read()
    user = re.search(r'^homelab_user:\s*"?([^"\n]+)"?', settings, re.M)
    if user is None:
        user = re.search(r"^vault_homelab_user:\s*(.+)$", vault, re.M)
    return user.group(1).strip().strip("\"'"), password


def servarr_key(stack, app):
    with open(f"{SRV}/{stack}/appdata/{app}/config.xml") as file:
        return re.search(r"<ApiKey>([^<]+)", file.read()).group(1)


def servarr(app, port, version, stack):
    key = servarr_key(stack, app)
    out = {}
    endpoints = ["customformat", "qualityprofile", "downloadclient", "indexer", "notification", "importlist", "tag",
                 "delayprofile", "releaseprofile", "rootfolder", "remotepathmapping", "autotagging", "metadata",
                 "config/host", "config/mediamanagement", "config/naming", "config/indexer", "config/ui",
                 "config/downloadclient", "indexerproxy", "applications", "appprofile", "qualitydefinition"]
    for endpoint in endpoints:
        try:
            out[endpoint] = clean(get(f"http://127.0.0.1:{port}/api/{version}/{endpoint}", {"X-Api-Key": key}))
        except Exception:
            pass   # an endpoint this app does not have
    return out


def qbittorrent(user, password):
    base = "http://127.0.0.1:8080/api/v2"
    request = urllib.request.Request(base + "/auth/login", data=urllib.parse.urlencode({"username": user, "password": password}).encode())
    cookie = urllib.request.urlopen(request, timeout=20).headers.get("Set-Cookie", "").split(";")[0]
    prefs = get(base + "/app/preferences", {"Cookie": cookie})
    for volatile in ("web_ui_session_timeout", "locale", "save_path"):
        prefs.pop(volatile, None) if volatile == "locale" else None
    return clean(prefs)


def jellyfin(user, password):
    base = "http://127.0.0.1:8096"
    auth = 'MediaBrowser Client="dump", Device="dump", DeviceId="dump", Version="1"'
    token = get(base + "/Users/AuthenticateByName", {"Authorization": auth, "Content-Type": "application/json"},
                json.dumps({"Username": user, "Pw": password}).encode())["AccessToken"]
    headers = {"Authorization": f'{auth}, Token="{token}"'}
    out = {}
    for name, path in (("configuration", "/System/Configuration"), ("branding", "/System/Configuration/branding"),
                       ("encoding", "/System/Configuration/encoding"), ("libraries", "/Library/VirtualFolders"),
                       ("plugins", "/Plugins"), ("repositories", "/Repositories"), ("api_key_apps", "/Auth/Keys")):
        try:
            data = get(base + path, headers)
            if name == "api_key_apps":
                data = sorted(k["AppName"] for k in data["Items"])
            if name == "plugins":
                data = sorted((p["Name"], p["Status"]) for p in data)
            out[name] = clean(data)
        except Exception:
            pass
    users = get(base + "/Users", headers)
    out["users"] = sorted(u["Name"] for u in users)
    out["user_policies"] = {u["Name"]: clean({k: v for k, v in u["Policy"].items() if k in ("IsAdministrator", "IsHidden", "IsDisabled", "EnableAllFolders", "EnableMediaPlayback")}) for u in users}
    return out


def seerr():
    with open(f"{SRV}/media-stack/appdata/seerr/settings.json") as file:
        settings = json.load(file)
    key = settings["main"]["apiKey"]
    headers = {"X-Api-Key": key}
    base = "http://127.0.0.1:5055/api/v1/settings"
    out = {}
    for name in ("main", "radarr", "sonarr", "jellyfin", "public", "notifications/telegram"):
        try:
            out[name] = clean(get(f"{base}/{name}", headers))
        except Exception:
            pass
    out["settings_file"] = clean({k: v for k, v in settings.items() if k in ("main", "notifications", "jellyfin")})
    return out


def bazarr():
    import yaml  # noqa: PLC0415 (only here: the config of Bazarr is YAML)
    with open(f"{SRV}/media-stack/appdata/bazarr/config/config.yaml") as file:
        key = yaml.safe_load(file)["auth"]["apikey"]
    out = {}
    for name in ("settings", "languages/profiles"):
        out[name] = clean(get(f"http://127.0.0.1:6767/api/system/{name}", {"X-API-KEY": key}))
    return out


def cleanuparr(user, password):
    base = "http://127.0.0.1:11011/api"
    token = get(base + "/auth/login", {"Content-Type": "application/json"}, json.dumps({"username": user, "password": password}).encode())["tokens"]["accessToken"]
    headers = {"Authorization": f"Bearer {token}"}
    out = {}
    for name in ("download_client", "radarr", "sonarr", "queue_cleaner"):
        try:
            out[name] = clean(get(f"{base}/configuration/{name}", headers))
        except Exception:
            pass
    out["stall_rules"] = clean(get(f"{base}/queue-rules/stall", headers))
    return out


def maintainerr():
    base = "http://127.0.0.1:6246/api"
    out = {}
    for name in ("settings/jellyfin", "settings/seerr", "settings/radarr", "settings/sonarr", "notifications/configurations"):
        out[name] = clean(get(f"{base}/{name}"))
    out["rules"] = sorted(r["name"] for r in get(f"{base}/rules"))
    return out


def suggestarr(user, password):
    base = "http://127.0.0.1:5000/api"
    token = get(base + "/auth/login", {"Content-Type": "application/json"}, json.dumps({"username": user, "password": password}).encode())["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    out = {"config": clean(get(base + "/config/fetch", headers))}
    out["jobs"] = clean([{k: v for k, v in j.items() if k in ("name", "filters", "enabled", "schedule_type", "schedule_value", "media_type")}
                         for j in get(base + "/jobs", headers)["jobs"]])
    return out


def grafana(user, password):
    import base64  # noqa: PLC0415
    headers = {"Authorization": "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()}
    base = "http://127.0.0.1:3000/api"
    out = {}
    for name, path in (("datasources", "/datasources"), ("folders", "/folders"), ("alert_rules", "/v1/provisioning/alert-rules"),
                       ("contact_points", "/v1/provisioning/contact-points")):
        try:
            out[name] = clean(get(base + path, headers))
        except Exception:
            pass
    out["dashboards"] = sorted(d["title"] for d in get(base + "/search?type=dash-db", headers))
    return out


def homepage():
    out = {}
    folder = f"{SRV}/monitoring-stack/appdata/homepage"
    for name in ("services.yaml", "settings.yaml", "bookmarks.yaml", "widgets.yaml", "custom.css"):
        try:
            with open(f"{folder}/{name}") as file:
                text = file.read()
        except FileNotFoundError:
            continue
        # The keys of widgets and logins are secrets; what is left is the layout.
        out[name] = re.sub(r"(?im)^(\s*(?:key|password|username|apikey|token)\s*:).*$", r"\1 <hidden>", text)
    return out


def main():
    user, password = login_details()
    steps = {
        "radarr": lambda: servarr("radarr", 7878, "v3", "media-stack"),
        "sonarr": lambda: servarr("sonarr", 8989, "v3", "media-stack"),
        "prowlarr": lambda: servarr("prowlarr", 9696, "v1", "download-stack"),
        "qbittorrent": lambda: qbittorrent(user, password),
        "jellyfin": lambda: jellyfin(user, password),
        "seerr": seerr,
        "bazarr": bazarr,
        "cleanuparr": lambda: cleanuparr(user, password),
        "maintainerr": maintainerr,
        "suggestarr": lambda: suggestarr(user, password),
        "grafana": lambda: grafana(user, password),
        "homepage": homepage,
    }
    dump = {}
    for app, step in steps.items():
        try:
            dump[app] = step()
        except Exception as error:   # an app that is not installed, or does not answer
            dump[app] = {"not dumped": type(error).__name__}
    json.dump(dump, sys.stdout, indent=1, sort_keys=True, default=str)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
