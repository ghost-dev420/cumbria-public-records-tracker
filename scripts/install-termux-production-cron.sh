#!/data/data/com.termux/files/usr/bin/bash
set -Eeuo pipefail

# Run ONCE in native Termux after setting up the Ubuntu collector.
PREFIX="${PREFIX:-/data/data/com.termux/files/usr}"
export PATH="$PREFIX/bin:/system/bin:${PATH:-}"
if ! command -v pkg >/dev/null 2>&1; then
  echo "This installer must run in native Termux, not inside Ubuntu." >&2
  exit 2
fi

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
runner="$script_dir/run-termux-production.sh"
test -f "$runner"
if [[ "$runner" == *[[:space:]]* || "$HOME" == *[[:space:]]* ]]; then
  echo "Cron setup requires a Termux/repository path without whitespace." >&2
  exit 2
fi

pkg install -y cronie termux-services
mkdir -p "$HOME/.local/state/cumbria" "$HOME/.termux/boot"

# Preserve unrelated crontab entries; replace only this managed job.
marker='# cumbria-public-records-production'
tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT
(crontab -l 2>/dev/null || true) | grep -Fv "$marker" > "$tmp" || true
printf '19 4 * * * %s/bin/bash %s >> %s/.local/state/cumbria/production.log 2>&1 %s\n' \
  "$PREFIX" "$runner" "$HOME" "$marker" >> "$tmp"
crontab "$tmp"

# Termux:Boot is optional but recommended for restarting cron after reboot.
# Its app must be installed and launched once separately by the device owner.
cat > "$HOME/.termux/boot/90-cumbria-crond" <<'BOOT'
#!/data/data/com.termux/files/usr/bin/sh
termux-wake-lock || true
if [ -r /data/data/com.termux/files/usr/etc/profile.d/start-services.sh ]; then
  . /data/data/com.termux/files/usr/etc/profile.d/start-services.sh
fi
sv up crond || true
BOOT
chmod 700 "$HOME/.termux/boot/90-cumbria-crond"

if command -v sv-enable >/dev/null 2>&1; then
  sv-enable crond || true
fi
if command -v sv >/dev/null 2>&1; then
  sv up crond || true
fi

echo "Daily production collector scheduled for 04:19 in the phone's local timezone."
echo "Persistent DB and raw evidence remain inside Ubuntu proot."
echo "Run now: bash '$runner'"
echo "Log: $HOME/.local/state/cumbria/production.log"
echo "If 'sv status crond' is not running, restart Termux and run: sv-enable crond && sv up crond"
echo "For reboot persistence, install and open Termux:Boot once, and exempt Termux from battery optimisation."
