#!/usr/bin/env bash
# Writes a first configuration in ~/homelab-config by asking a few questions: the login for the
# apps, an email, and which optional apps you want. Called by setup.sh when there is no
# configuration yet; never overwrites a file that exists. Run it again for another folder:
#   CONFIG_DIR=/path ./init-config.sh
set -euo pipefail
cd "$(dirname "$0")"

config_dir="${CONFIG_DIR:-$HOME/homelab-config}"
fail() { echo "❌ $1" >&2; exit 1; }

[ -f "$config_dir/vault.yml" ] && fail "$config_dir/vault.yml exists already; edit it with: ansible-vault edit config/vault.yml"
command -v python3 >/dev/null || fail "python3 is needed (sudo apt install python3)"

# Answers come from the terminal, or from a pipe (one answer per line) for a scripted install.
ask() { # ask "question" default -> $answer
  local prompt="$1" default="${2:-}"
  if [ -n "$default" ]; then printf '%s [%s]: ' "$prompt" "$default"; else printf '%s: ' "$prompt"; fi
  IFS= read -r answer || fail "No answer to: $prompt"
  answer="${answer:-$default}"
}
ask_secret() { # ask_secret "question" -> $answer, not shown on screen
  printf '%s: ' "$1"
  if [ -t 0 ]; then IFS= read -r -s answer || fail "No answer to: $1"; echo; else IFS= read -r answer || fail "No answer to: $1"; fi
}
yes_no() { # yes_no "question" default(y/n) -> $answer true/false
  local default="$2"
  while true; do
    ask "$1 (y/n)" "$default"
    case "${answer,,}" in y|yes) answer=true; return;; n|no) answer=false; return;; esac
    echo "Answer y or n."
  done
}

echo "A first configuration, written to $config_dir. Press Enter to accept the answer in brackets."
echo

ask "Login name for the apps (also the server user that owns the app folders)" "$USER"
user="$answer"
while true; do
  ask_secret "Password for every app (at least 8 characters)"
  password="$answer"
  [ "${#password}" -ge 8 ] && break
  echo "Too short."
done
ask "Email address for Seerr's admin" ""
email="$answer"
[ -n "$email" ] || fail "An email address is needed."

backup_password="$(openssl rand -base64 24 2>/dev/null | tr -d '/+=\n')"
[ -n "$backup_password" ] || fail "openssl is needed (sudo apt install openssl)"

echo
echo "Optional apps (the search, download and watch apps are always installed):"
yes_no "  Bazarr, subtitles" y;                         bazarr=$answer
yes_no "  Grafana and Prometheus, graphs" y;            monitoring=$answer
yes_no "  Homepage, one page with every app" y;         homepage=$answer
yes_no "  Calibre-Web, ebooks (with Kobo sync)" y;      books=$answer
yes_no "  SuggestArr, suggestions from what you watch (needs a free TMDb API key)" n; suggestarr=$answer
tmdb_key=""
if [ "$suggestarr" = true ]; then
  ask "    TMDb API key (themoviedb.org, Settings, API; Enter to skip SuggestArr)" ""
  tmdb_key="$answer"
  [ -n "$tmdb_key" ] || { suggestarr=false; echo "    No key: SuggestArr is left out."; }
fi
yes_no "  Tailscale, to reach the apps away from home" y; tailscale=$answer

mkdir -p "$config_dir"
umask 077
# JSON strings are valid YAML, so quotes and backslashes in a password are safe.
CONFIG_USER="$user" CONFIG_PASSWORD="$password" CONFIG_EMAIL="$email" CONFIG_BACKUP="$backup_password" CONFIG_TMDB="$tmdb_key" \
  python3 - "$config_dir/vault.yml" <<'PY'
import json, os, sys
lines = [
    "---",
    "# Written by init-config.sh. ./setup.sh encrypts it; change it later with:",
    "#   ansible-vault edit config/vault.yml",
    f"vault_homelab_user: {json.dumps(os.environ['CONFIG_USER'])}",
    f"vault_homelab_password: {json.dumps(os.environ['CONFIG_PASSWORD'])}",
    f"vault_admin_email: {json.dumps(os.environ['CONFIG_EMAIL'])}",
    f"vault_backup_password: {json.dumps(os.environ['CONFIG_BACKUP'])}",
    "# More options (Telegram, subtitle accounts, automatic deploys): config.example/vault.yml",
]
if os.environ["CONFIG_TMDB"]:
    lines.append(f"vault_tmdb_api_key: {json.dumps(os.environ['CONFIG_TMDB'])}")
lines.append("")
open(sys.argv[1], "w").write("\n".join(lines))
PY
umask 022
if [ ! -f "$config_dir/settings.yml" ]; then
  cat >"$config_dir/settings.yml" <<SETTINGS
---
# Your own choices, overriding inventory/group_vars/homelab/main.yml and roles/*/defaults.
# More options: config.example/settings.yml
homelab_bazarr: $bazarr
homelab_monitoring: $monitoring
homelab_homepage: $homepage
homelab_books: $books
homelab_suggestarr: $suggestarr
homelab_tailscale: $tailscale
SETTINGS
fi

echo
echo "✅ Written: $config_dir/vault.yml and settings.yml"
echo "The backup password was made for you and is in vault.yml (vault_backup_password): ./setup.sh encrypts that file"
echo "and shows you its password once. Keep a copy of both outside this server, or the backups cannot be opened."
