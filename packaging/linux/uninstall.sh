#!/usr/bin/env bash
# Remove the Ovigyan connector service. Settings and queued punches in /var/lib/ovigyan-connector are kept
# unless you add --purge.
set -euo pipefail

purge=0
prefix=""
use_systemctl=1
while [ $# -gt 0 ]; do
    case "$1" in
        --purge) purge=1; shift ;;
        --prefix) prefix="${2:?--prefix needs a directory}"; shift 2 ;;
        --no-systemctl) use_systemctl=0; shift ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
done
if [ -z "$prefix" ] && [ "$(id -u)" -ne 0 ]; then
    echo "Run as root:  sudo ./ovigyan-connector-uninstall.sh" >&2
    exit 1
fi

if [ "$use_systemctl" = 1 ]; then
    systemctl disable --now ovigyan-connector.service 2>/dev/null || true
fi
rm -f "$prefix/etc/systemd/system/ovigyan-connector.service" "$prefix/usr/local/bin/ovigyan-connector"
if [ "$use_systemctl" = 1 ]; then
    systemctl daemon-reload
fi
if [ "$purge" = 1 ]; then
    rm -rf "$prefix/var/lib/ovigyan-connector"
    if [ -z "$prefix" ]; then userdel ovigyan-connector 2>/dev/null || true; fi
fi
echo "Removed the Ovigyan connector service."
