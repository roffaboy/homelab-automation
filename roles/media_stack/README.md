# media_stack

The Compose project for the library: Jellyfin, Seerr, Radarr, Sonarr, Recyclarr,
[Maintainerr](https://github.com/maintainerr/maintainerr) and, optionally, Bazarr.

- **Runs:** always. Tag: `media`. Bazarr with `homelab_bazarr: true`, [HolyArrs](https://github.com/HolyArrs/HolyArrs) with `homelab_holyarrs: true` (to try next to them, with a library of its own; `app_config` connects it).
- **Settings:** `defaults/main.yml`: the image versions (updated by Renovate) and how often
  Recyclarr syncs.
- **Note:** Jellyfin gets the Intel GPU (`/dev/dri`) for hardware transcoding; the apps are
  configured by `app_config`.
