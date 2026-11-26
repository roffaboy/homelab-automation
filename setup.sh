#!/usr/bin/env bash
# Sets up the server and installs everything: checks, questions (init-config.sh), Ansible,
# Tailscale, ~/homelab-config as config/, encrypted secrets, then the playbook. Safe to run again.
#
#   ./setup.sh                  set up and install
#   ./setup.sh --no-provision   set up only
#   ./setup.sh <options>        other options go to ansible-playbook (--tags jellyfin, --check, ...)
set -euo pipefail
cd "$(dirname "$0")"

config_dir="${CONFIG_DIR:-$HOME/homelab-config}"
provision=true
playbook_args=()
for arg in "$@"; do
  if [ "$arg" = --no-provision ]; then provision=false; else playbook_args+=("$arg"); fi
done

fail() { echo "❌ $1" >&2; exit 1; }
encrypted() { head -c 14 "$1" | grep -q '^\$ANSIBLE_VAULT'; }

echo "== Checking the server"
[ "$(id -u)" -ne 0 ] || fail "Run this as your own user, not as root: it uses sudo where it needs to (the apps' folders belong to your user)."
. /etc/os-release
case "${ID:-}:${VERSION_ID:-}" in
  ubuntu:24.04|ubuntu:26.04) ;;
  *) echo "⚠️  This is ${PRETTY_NAME:-an unknown system}; it is tested on Ubuntu Server 24.04 and 26.04." ;;
esac
curl -fsS --max-time 15 -o /dev/null https://github.com || fail "No internet connection (github.com can't be reached)."
# The password is asked once, here; the playbook runs for a while and needs sudo throughout.
sudo -n true 2>/dev/null || sudo -v || fail "Your user needs sudo."
( while kill -0 $$ 2>/dev/null; do sudo -n true; sleep 50; done ) >/dev/null 2>&1 </dev/null &

if [ ! -f config/vault.yml ] && [ ! -f "$config_dir/vault.yml" ]; then
  [ -e config ] && fail "config/ has no vault.yml. Copy config.example/vault.yml there and fill it in."
  echo "No configuration yet: a few questions first."
  CONFIG_DIR="$config_dir" ./init-config.sh
fi
[ -e config ] || ln -s "$config_dir" config
[ -f config/vault.yml ] || fail "No config/vault.yml. Copy config.example/vault.yml and fill it in."

echo "== Ansible"
sudo apt-get install -y -qq git python3-venv curl openssl >/dev/null
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt
.venv/bin/ansible-galaxy collection install -r collections/requirements.yml >/dev/null

echo "== Encrypting your secrets"
if [ ! -f .vault_pass ]; then
  encrypted config/vault.yml && fail "config/vault.yml is encrypted: put its password in .vault_pass."
  (umask 077; openssl rand -base64 32 >.vault_pass)
  echo "New vault password in .vault_pass. Save it in your password manager: $(cat .vault_pass)"
fi
# The other files hold nothing secret and stay readable.
for file in config/vault.yml config/wg0.conf; do
  if [ -f "$file" ] && ! encrypted "$file"; then
    .venv/bin/ansible-vault encrypt "$file"
  fi
done

tailscale_wanted=false
if ! grep -qs 'homelab_tailscale: *false' config/*.yml; then
  tailscale_wanted=true
  if ! command -v tailscale >/dev/null; then
    echo "== Tailscale"
    curl -fsSL https://tailscale.com/install.sh | sh
    sudo tailscale up
  fi
fi

if [ "$provision" = false ]; then
  echo
  echo "✅ Ready. Install: ./setup.sh"
  exit 0
fi

echo "== Installing the apps"
.venv/bin/ansible-playbook provision.yml "${playbook_args[@]}" ||
  fail "The playbook stopped. Fix what it says above and run ./setup.sh again."

ip=$(hostname -I | awk '{print $1}')
echo
echo "✅ Done. Seerr: http://$ip:5055, Jellyfin: http://$ip:8096"
echo "   Still to do:"
echo "   - Keep .vault_pass and vault.yml outside this server."
if [ "$tailscale_wanted" = true ]; then
  echo "   - Approve the server's route in the Tailscale admin console."
fi
[ -f config/indexers.yml ] || echo "   - Add your indexers (config/indexers.yml)."
