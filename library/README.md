# library

Ansible modules of this repo, for apps that Ansible has no module for. Each one reads first and
only writes what differs, so a second run reports "ok", and each supports `--check`. How each
works is at the top of its file. `rest_settings` and `api_resource` have unit tests (`tests/`, run by CI).

| Module | For | Used in |
|---|---|---|
| [`servarr_resource`](servarr_resource.py) | One item in Radarr, Sonarr or Prowlarr (a download client, a root folder, an indexer, a setting) through their API | `roles/app_config/tasks/arrs.yml`, `prowlarr.yml`, `access.yml` |
| [`qbittorrent_config`](qbittorrent_config.py) | qBittorrent's preferences and download categories through its Web API | `roles/app_config/tasks/qbittorrent.yml` |
| [`rest_settings`](rest_settings.py) | A group of settings that an app's REST API serves as one JSON object (read it, set the wanted keys, write it back only when something differs) | `roles/app_config/tasks/seerr.yml` |
| [`api_resource`](api_resource.py) | One item of a list an app's REST API serves (find it by a key, create it when missing, save it when it differs, delete it): the app is brought in line with what Ansible knows | `roles/app_config/tasks/maintainerr.yml`, `cleanuparr.yml` |
| [`calibre_web`](calibre_web.py) | Calibre-Web's login, Kobo sync and users, in its database (it has no API for them) | `roles/app_config/tasks/calibre.yml` |
