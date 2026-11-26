# homelab-automation

[![CI](https://img.shields.io/github/actions/workflow/status/roffaboy/homelab-automation/ci.yml?branch=main&style=flat&logo=githubactions&logoColor=white&label=CI)](https://github.com/roffaboy/homelab-automation/actions/workflows/ci.yml) [![Release](https://img.shields.io/github/v/release/roffaboy/homelab-automation?style=flat&logo=github&logoColor=white&label=Release)](https://github.com/roffaboy/homelab-automation/releases/latest) [![Last commit](https://img.shields.io/github/last-commit/roffaboy/homelab-automation?style=flat&logo=github&logoColor=white&label=Last%20commit)](https://github.com/roffaboy/homelab-automation/commits/main) [![Renovate](https://img.shields.io/badge/Renovate-enabled-308BE3?style=flat&logo=renovate&logoColor=white)](renovate.json) [![License](https://img.shields.io/github/license/roffaboy/homelab-automation?style=flat&label=License)](LICENSE)

Ansible for a single Ubuntu home server: request a movie or series, it downloads and shows up
in Jellyfin. The playbook installs the apps below in Docker, connects them to each other and
publishes their web UIs on your home network, and over Tailscale away from home.

Installed: [Jellyfin](https://jellyfin.org) and [Seerr](https://github.com/seerr-team/seerr) (watch and
request); [Prowlarr](https://prowlarr.com), [Radarr](https://radarr.video), [Sonarr](https://sonarr.tv),
[qBittorrent](https://www.qbittorrent.org), [Cleanuparr](https://github.com/Cleanuparr/Cleanuparr),
[Maintainerr](https://github.com/maintainerr/maintainerr) and [Recyclarr](https://recyclarr.dev) (find,
download and tidy up). Optional: [Bazarr](https://www.bazarr.media),
[SuggestArr](https://github.com/giuseppe99barchetta/SuggestArr),
[Calibre-Web](https://github.com/crocodilestick/Calibre-Web-Automated),
[Grafana](https://grafana.com) with [Prometheus](https://prometheus.io), and
[Homepage](https://gethomepage.dev). Built on Ansible, Docker, Tailscale and WireGuard.

Each optional app has its own switch ([Options](#options)): subtitles, ebooks with Kobo sync
(Calibre-Web Automated), suggestions from what you watch (SuggestArr), graphs and a start page.
WireGuard is used when you add a VPN config.

Runs on an empty server and on an existing one: app data is kept and only settings that
differ from this repo are changed. One username and password is used for every service.

No sites to download from are included: you add the indexers you choose yourself. Only
download what you are allowed to where you live.

## Before you start

You need a server with Ubuntu Server 24.04 or 26.04, a user with `sudo`, and a fixed address for it in
your router (a DHCP reservation). For hardware transcoding an Intel GPU helps; without one Jellyfin
transcodes in software.

The rest depends on what you want. Have it ready before you start; the script asks for the basics and
reads the rest from `~/homelab-config`:

| You want | You need | Goes in |
|---|---|---|
| Downloads through a VPN | A WireGuard config from your VPN provider | `wg0.conf` |
| The apps away from home | A [Tailscale](https://tailscale.com) account | asked by the script |
| Telegram messages and bot | A Telegram bot token and your chat id | `vault.yml` |
| Suggestions (SuggestArr) | A free [TMDb](https://www.themoviedb.org/settings/api) API key | asked by the script |
| More subtitles | Accounts at [OpenSubtitles](https://www.opensubtitles.com), [SubDL](https://subdl.com), [Subsource](https://subsource.net) | `vault.yml` |
| Offsite backup | A cloud drive that rclone supports, e.g. Google Drive ([steps](#offsite-backup)) | `vault.yml`, `settings.yml` |
| Automatic deploys | Your own fork of this repo on GitHub | `vault.yml` |
| Search sites in Prowlarr | The indexers you choose | `indexers.yml` |

Every option is explained in [`config.example/`](config.example). Without a file, that part isn't set up.

## Install

```bash
sudo apt install -y git
git clone https://github.com/roffaboy/homelab-automation.git && cd homelab-automation
./setup.sh
```

The script asks for the login for the apps, an email and which optional apps you want, then installs
everything. Run it again whenever you like. To write the configuration yourself, copy
`config.example/*` to `~/homelab-config/` first.

The apps are then at `http://<server-ip>:<port>`:

| App | Port |
|---|---|
| Seerr (request movies and series) | `:5055` |
| Jellyfin (watch) | `:8096` |
| SuggestArr (suggestions) | `:5000` |
| Homepage (every app on one page) | `:3001` |
| Calibre-Web (ebooks) | `:8083` |
| Radarr `:7878`, Sonarr `:8989`, Bazarr `:6767`, Prowlarr `:9696`, qBittorrent `:8080`, Cleanuparr `:11011`, Maintainerr `:6246`, Grafana `:3000` | |

Log in with the username and password you gave. With Tailscale, approve the server's route once
([Away from home](#away-from-home)).

## Your configuration

This repo is the same for everyone. Everything that is yours lives in `config/`, a link to
`~/homelab-config` (keep it as a private git repo), never in this repo. Start from
[`config.example/`](config.example); each file explains its keys. Every `.yml` in it is read,
so split it up as you like. Leave a file out and that part isn't configured.

- **`vault.yml`** (encrypted): passwords and API keys, and the extra users of Jellyfin and
  Calibre-Web. Required: `vault_homelab_password` (the login for every app) and
  `vault_backup_password`.
- **`settings.yml`**: your choices. Required: `homelab_user` (your Ubuntu user) and
  `homelab_admin_email` (Seerr's admin). Also which optional apps, time zone, subtitle languages
  (see Options). The user and the email are here and not in the vault on purpose: Ansible hides
  everything from the vault as `$REDACTED$` in its output, a user name included. A configuration
  with `vault_homelab_user` and `vault_admin_email` in the vault still works.
- **`indexers.yml`**: the sites Prowlarr searches. Per site a `name` and its `definition`, the
  file name (without `.yml`) in [Prowlarr's indexer list](https://github.com/Prowlarr/Indexers/tree/master/definitions):
  `- { name: My indexer, definition: <id> }`. Indexers added in Prowlarr itself are kept.
- **`maintainerr.yml`**: rules for removing what has been watched. The example removes movies 1
  day and complete seasons 3 days after the last view; paste your own from Maintainerr's
  Rules > Export.
- **`jellyfin.yml`**: one home screen for every user, profile tiles on the login screen,
  profile pictures (files next to it, e.g. `images/`).
- **`wg0.conf`** (encrypted): optional, the WireGuard file from your VPN provider, as downloaded.

`setup.sh` encrypts `vault.yml` and `wg0.conf`; change those later with
`ansible-vault edit config/<file>`. The others are plain text.

The vault password is in `.vault_pass` (not in git). Keep it in your password manager: with it,
the encrypted files are useless to anyone else, and without it they are useless to you.

With automatic deploys (Extras), a commit to your config repo is deployed too: the server pulls
it every 10 minutes, with your own SSH key. On a new server, clone it to `~/homelab-config` and
put the vault password in `.vault_pass` before `./setup.sh`.

## Options

Choose in `config/settings.yml`, then run the playbook again.

| Want | Do |
|---|---|
| A VPN for downloads | Put your provider's WireGuard config in `config/wg0.conf` and run `./setup.sh` (it encrypts it). Without it qBittorrent downloads from your own IP address. A port forwarded by the provider: `vault_torrent_port` |
| No Tailscale | `homelab_tailscale: false`: the apps only at home, nothing from outside |
| No subtitles | `homelab_bazarr: false` |
| No graphs | `homelab_monitoring: false` (Grafana, Prometheus) |
| No start page | `homelab_homepage: false` |
| No ebooks | `homelab_books: false` |
| Suggestions | `homelab_suggestarr: true`, then set it up at `:5000` (below) |
| Other subtitle languages | `app_config_bazarr_profile: { name: German and English, languages: [de, en] }` |
| Indexers | `config/indexers.yml`, or add them in Prowlarr (those are kept) |
| More settings | Any setting in `inventory/group_vars/homelab/main.yml` or `roles/*/defaults/main.yml` |

More subtitles: an [opensubtitles.com](https://www.opensubtitles.com) account
(`vault_opensubtitles_username`, `vault_opensubtitles_password`) and API keys for
[SubDL](https://subdl.com) and [Subsource](https://subsource.net) (`vault_subdl_api_key`,
`vault_subsource_api_key`) in the vault.

SuggestArr needs a free [TMDb API key](https://www.themoviedb.org/settings/api) in the vault
(`vault_tmdb_api_key`); the playbook connects it to Jellyfin and Seerr, and every suggestion waits
for your approval. To add your [Simkl](https://simkl.com) history: a Simkl app's Client ID
(`vault_simkl_client_id`, app type "TV, devices & command line"), and the playbook installs
Jellyfin's Simkl plugin and gives SuggestArr the app. Link your Simkl account in both once, with a
PIN: Jellyfin (Dashboard, Plugins, Simkl; leave its import off) and SuggestArr (Profile).
Suggestions follow `app_config_suggestarr_jellyfin_user` (your login by default). An [OMDb
key](https://www.omdbapi.com/apikey.aspx) (`vault_omdb_api_key`) also filters on IMDb ratings. Jobs
you make in SuggestArr.

## Usage

```bash
./setup.sh
```

- Run part of it with `./setup.sh --tags docker|download|media|books|monitoring|apps|tailscale`, or one app with
  `--tags qbittorrent|jellyfin|arrs|prowlarr|cleanuparr|recyclarr|seerr|maintainerr|bazarr|suggestarr|calibre|homepage`.
- Preview with `./setup.sh --check --diff` (once Docker is installed).
- `./reset.sh` wipes the server back to Ubuntu + Tailscale (`--keep-media` keeps the library).
- Family members only need a Jellyfin user, with their own watch history: `vault_jellyfin_users`
  in the vault, or make one in Jellyfin (Dashboard, Users). They log in with it on the TV.
  Such a user is not an admin and sees every library. Every Jellyfin user also gets a Seerr user,
  so they can request movies and series; by default a request waits for an admin to approve it.
  In `config/settings.yml` you choose, by Jellyfin user name, who gets more:
  `app_config_seerr_auto_approve_users` (their requests go through by themselves) and
  `app_config_seerr_admin_users` (everything in Seerr: settings, clearing a title's data, other
  people's requests). The first admin is the login you set up the server with.
- Ebooks: put them in `~/srv/media-data/books-ingest`; Calibre-Web imports them into the library.
  For a Kobo: open Calibre-Web at `http://<server-ip>:8083`, Profile, Kobo Sync Token, and copy
  the `api_endpoint` line into `.kobo/Kobo/Kobo eReader.conf` on the Kobo (USB), under
  `[OneStoreServices]`. The Kobo syncs on your home wifi. Each reader can get their own user
  with their own token: `homelab_calibre_users` in `settings.yml` (see the example there), or Admin, Add new user.

## Extras

All optional; without them nothing of it is installed. None of the above needs GitHub.

- **Telegram** (`vault_telegram_bot_token`, `vault_telegram_chat_id`): imports, errors and app
  updates from Radarr, Sonarr, Prowlarr and Seerr, and Grafana alerts (full disk, heat,
  crashing containers).
- **Automatic deploys** (`vault_homelab_repo`, the SSH address of your fork on GitHub): the server
  checks `main` every 10 minutes and runs the playbook when it changed. Add
  `~/.ssh/homelab_deploy.pub` (made by the first run) as a deploy key on your fork. Before new
  images it backs up the app data and the ebook library with [restic](https://restic.net) to
  `/backup/restic`; when the run fails, it puts that backup and the previous commit back. Log:
  `journalctl -u homelab-autodeploy`.
  With Telegram too: a bot for `/deploy`, `/status` and `/log`, which also warns when an app is
  stopped or unhealthy for 5 minutes. Running the playbook yourself
  from a branch puts the automatic deploys on hold, so `main` doesn't replace what you test;
  from `main` they follow it again. In Telegram: `/hold`, and `/main` to deploy `main` now.
- **GitHub card on Homepage** (`vault_github_token`, with automatic deploys): a read-only
  fine-grained token (Actions, Contents, Pull requests) for your fork.
- **Away from home** (Tailscale, on unless `homelab_tailscale: false`, see below): the same
  addresses at home and away, and casting to a Chromecast at home.
- **Offsite backup** (`homelab_backup_remote`, see below): every night the app data and the
  ebook library, encrypted, to the cloud.

### Away from home

Tailscale routes the server's LAN address to it: only that one address (a /32), not your home
network. So `http://<LAN address>:<port>` is the one address for every app, at home over Wi-Fi
and away with Tailscale on. A Chromecast plays from that address too; it isn't on your tailnet
and asks Google's DNS, so a `ts.net` or home DNS name gets as far as "Ready to cast" and then
nothing plays.

**Set it up:**

1. Give the server a fixed address in your router (a DHCP reservation).
2. Run the playbook. It keeps IP forwarding on and adds `<LAN address>/32` to the routes the
   server advertises; other routes and exit-node settings stay as they are.
3. Approve the route once: [admin console](https://login.tailscale.com/admin/machines) → the
   server → *Edit route settings* → tick the route. Or let it approve itself with
   [`autoApprovers`](https://tailscale.com/kb/1337/policy-syntax#autoapprovers) in your
   tailnet policy.
4. In the Jellyfin app: one server, `http://<LAN address>:8096` (e.g. `http://192.168.1.51:8096`).

**Use it:** at home, Tailscale off: everything over Wi-Fi, and the cast button plays on the
Chromecast. Away, Tailscale on: the same addresses. In the Android Tailscale app, *Use Tailscale
subnets* must be on (the default). Casting to a Chromecast away from home is not part of this.

**Test it:** at home, Tailscale off, cast a video: it should play on the TV. Then on mobile data
with Tailscale on, open the app: the same server loads and plays. Checks on the server:
`tailscale debug prefs | grep -A2 AdvertiseRoutes` shows the route, and in the admin console it
must say approved, not "awaiting approval".

**Not working?** Away: is Tailscale on and the route approved, and does the server still have that
LAN address? At home: is the phone on Wi-Fi (not mobile data) and on the same network as the
Chromecast? With an access-control policy of your own, it must allow your devices to reach
`<LAN address>`.

### Offsite backup

Every night at 04:00 the apps stop while their data is copied to `/backup/staging` (seconds,
after the first time), then [restic](https://restic.net) backs up that copy (settings, watch
history, requests) and your ebooks, encrypted with `vault_backup_password`, through
[rclone](https://rclone.org) to Google Drive, OneDrive, Backblaze B2 or
[another cloud](https://rclone.org/overview/). Not the movies and series: Radarr and Sonarr get
them again. It is about 1–2 GB, and each night adds only what changed. It keeps 2 daily, 1
weekly and 1 monthly backup, never runs at the same time as an automatic deploy, and Telegram
tells you when it fails. Log: `journalctl -u homelab-backup`.

For Google Drive, once:

1. [Make your own client ID](https://rclone.org/drive/#making-your-own-client-id) (free): rclone's
   shared one is rate limited by Google. Publish the app ("In production"), or its login
   expires after 7 days.
2. On a computer with a browser, [install rclone](https://rclone.org/install/), run
   `rclone config`: a new remote `gdrive`, type `drive`, your client ID and secret, scope
   `drive.file` (only the files it makes itself, not the rest of your Drive).
3. `rclone config show gdrive` prints the section for `config/vault.yml`:

   ```yaml
   vault_rclone_config: |
     [gdrive]
     type = drive
     client_id = <your client ID>
     client_secret = <your client secret>
     scope = drive.file
     token = {"access_token":"…","refresh_token":"…",…}
   ```

4. The folder, in `config/settings.yml`: `homelab_backup_remote: "gdrive:Backup/homelab"`. Let
   rclone make it: with `drive.file` it can't see a folder you made yourself.

Other clouds: `rclone config` writes such a section too.

Getting things out, also on another computer with restic and rclone: put the repository and the
password in the environment (on the server: `sudo -i` and `set -a; . /etc/homelab-backup/env`),
then `restic snapshots` lists the backups, `restic restore latest --target ~/restore` puts
everything back as plain files under `~/restore/backup/staging/` (`--include '*.epub'` for only
the ebooks), and `restic mount ~/backup` lets you browse them as folders.

## Layout

The linked folders have a short README of their own.

| Path | Purpose |
|---|---|
| `provision.yml` | The playbook: the roles in order |
| `inventory/group_vars/homelab/main.yml` | Settings shared by several roles |
| `setup.sh` | Everything in one go: checks the server, Ansible, Tailscale, your configuration and its secrets, then the playbook |
| `config/` | Your configuration (not in this repo, see above); examples in [`config.example/`](config.example) |
| `roles/*/defaults/main.yml` | Settings of one role you may change |
| `roles/*/vars/main.yml` | Fixed values of one role |
| [`roles/docker`](roles/docker) | Docker Engine and Compose |
| [`roles/homelab_common`](roles/homelab_common) | Shared Docker network and the `/data` folders |
| [`roles/download_stack`](roles/download_stack), [`media_stack`](roles/media_stack), [`books_stack`](roles/books_stack), [`monitoring_stack`](roles/monitoring_stack) | One Docker Compose project each |
| [`roles/app_config`](roles/app_config) | Configures and connects the apps through their APIs, one task file per app |
| [`roles/tailscale`](roles/tailscale) | Keeps Tailscale updated, routes the server's LAN address over it |
| [`library/`](library) | Ansible modules for the Radarr/Sonarr/Prowlarr and qBittorrent APIs and Calibre-Web's database |
| [`filter_plugins/`](filter_plugins) | Password hash for qBittorrent's first-start config, Bazarr's settings form |
| [`roles/autodeploy`](roles/autodeploy) | Automatic deploys (Extras) |
| [`roles/backup`](roles/backup) | Offsite backup (Extras) |
| [`.github/workflows/`](.github/workflows), `renovate.json` | This repo's CI, releases and weekly image updates on GitHub; not needed to run it |

## What gets configured

| App | |
|---|---|
| qBittorrent | Automatic torrent management, a download category per app, 5 downloads at once |
| Radarr, Sonarr | Hardlinks, root folder, qBittorrent client, Jellyfin library refresh |
| Prowlarr | Your indexers (`config/indexers.yml`), FlareSolverr, sync to Radarr/Sonarr |
| Cleanuparr | Your login, qBittorrent, Radarr/Sonarr; removes downloads stuck on metadata (15 min) or stalled (30 min), then Radarr/Sonarr search again |
| Recyclarr | TRaSH Guides quality profiles (stock profiles removed), custom formats and naming: "Movies" takes 2160p, then 1080p, then 720p, up to ~10 GB a film; "Series" is WEB-1080p with its fallbacks. Optionally one more profile per language (`app_config_language_profiles`, e.g. "Movies - NL": only releases in that language are downloaded; TRaSH has no such profile, so Ansible builds it from TRaSH's language format) |
| Jellyfin | Admin account, more users (`vault_jellyfin_users`), optionally profile tiles, pictures and one home screen (`config/jellyfin.yml`), plugins (Intro Skipper, Jellyfin Enhanced, Simkl), libraries, hardware transcoding, API key for Radarr/Sonarr |
| Seerr | Jellyfin login, Radarr/Sonarr as default servers, optionally the discover languages and streaming region (`app_config_seerr_discover`), a Seerr user for every Jellyfin user and who is auto-approved or admin (`app_config_seerr_auto_approve_users`, `app_config_seerr_admin_users`), requests from Jellyfin's search (Jellyfin Enhanced) |
| Maintainerr | Jellyfin, Radarr, Sonarr, Seerr and Telegram, and your rule groups from `config/maintainerr.yml` (the example removes what has been watched, via a "Leaving soon" collection in Jellyfin). It has no login |
| Bazarr | Radarr/Sonarr connection, subtitles for everything in your languages (`app_config_bazarr_profile`, English by default), OpenSubtitles, SubDL and Subsource (optional, account or key in the vault). Optional (`homelab_bazarr`) |
| SuggestArr | Optional (`homelab_suggestarr`): your login, Jellyfin (and whose watching it follows), Seerr, TMDb, Simkl and OMDb, filters (`app_config_suggestarr_settings`), approval before anything is requested |
| Calibre-Web | Optional (`homelab_books`): your login instead of the default admin, Kobo sync with the Kobo store passed through, more users and their Kobo tokens (`vault_calibre_users`) |
| Grafana | Optional (`homelab_monitoring`): Prometheus datasource, and the dashboards you put in `config/grafana-dashboards/` (`home.json` there is the home page) |
| Homepage | Optional (`homelab_homepage`): every app with a link and a live widget (queues, streams, downloads), server load, and an Accounts tab with the outside services it uses (add your own: `app_config_accounts`) |

Anything not managed here, such as extra Jellyfin users and libraries or indexers you add by hand,
is left alone. Settings that are managed here are set back on the next run.

## Security

Meant for a home network behind your router's NAT, not for the internet.

- **Never forward a port to it.** Every web UI listens on all IPv4 addresses of the server
  (`homelab_publish_ip`). Docker's published ports bypass `ufw`, so the firewall will not help.
  Away from home, use Tailscale.
- **Homepage, Maintainerr and Prometheus have no login:** anyone on your Wi-Fi can open them. The
  other apps use the one username and password from `vault.yml`, so make it a long one.
- Secrets live in the encrypted `config/vault.yml`; the playbook keeps them out of its output
  (`no_log`) and out of the server's journal. The vault password is in `.vault_pass`, not in git.
- Two containers are more privileged than the rest: cAdvisor (`privileged`, to read container
  metrics) and autoheal (the Docker socket, to restart unhealthy containers; no network).
- Automatic deploys run the playbook, as your user with `sudo`, from the `main` branch of *your*
  fork, over a read-only deploy key. Whoever can push to that branch controls the server, so protect
  it (a branch ruleset, no force pushes).
- `reset.sh` wipes the app data, and the media unless you pass `--keep-media`; it asks you to type
  `reset` first. Provisioning keeps the app data. What deletes media is the cleanup you configure
  yourself: Maintainerr's rules and Cleanuparr.

## Contributing

Issues and pull requests are welcome. PR titles follow
[Conventional Commits](https://www.conventionalcommits.org) (`feat:`, `fix:`, `chore:`), since
the release notes and version come from them. CI runs ansible-lint and a full install on a fresh
VM for every PR.
