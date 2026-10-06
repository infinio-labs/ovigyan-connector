#!/usr/bin/env bash
# Install the Ovigyan connector as a systemd service. Run as root, next to the `ovigyan-connector` executable:
#   sudo ./ovigyan-connector-install.sh
# Then connect it:   sudo ovigyan-connector open      (prints a link; on a server, forward the port first:
#                                                       ssh -L 47890:127.0.0.1:47890 you@server)
# Options: --prefix DIR (stage the files under DIR instead of /; for packaging and tests)
#          --no-systemctl  --no-user  (skip those steps)
set -euo pipefail

prefix=""
use_systemctl=1
create_user=1
while [ $# -gt 0 ]; do
    case "$1" in
        --prefix) prefix="${2:?--prefix needs a directory}"; shift 2 ;;
        --no-systemctl) use_systemctl=0; shift ;;
        --no-user) create_user=0; shift ;;
        -h|--help) sed -n '2,8p' "$0"; exit 0 ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
done

here="$(cd "$(dirname "$0")" && pwd)"
binary="$here/ovigyan-connector"
unit_source="$here/ovigyan-connector.service"
[ -f "$unit_source" ] || unit_source="$here/../systemd/ovigyan-connector.service"
[ -f "$binary" ] || { echo "Put this script next to the ovigyan-connector executable." >&2; exit 1; }
[ -f "$unit_source" ] || { echo "ovigyan-connector.service not found next to this script." >&2; exit 1; }
if [ -z "$prefix" ] && [ "$(id -u)" -ne 0 ]; then
    echo "Run as root:  sudo ./ovigyan-connector-install.sh" >&2
    exit 1
fi

if [ "$create_user" = 1 ] && ! id ovigyan-connector >/dev/null 2>&1; then
    useradd --system --home-dir /var/lib/ovigyan-connector --shell /usr/sbin/nologin ovigyan-connector
fi

install -d -m 0755 "$prefix/usr/local/bin" "$prefix/etc/systemd/system"
install -m 0755 "$binary" "$prefix/usr/local/bin/ovigyan-connector"
install -m 0644 "$unit_source" "$prefix/etc/systemd/system/ovigyan-connector.service"

if [ "$use_systemctl" = 1 ]; then
    systemctl daemon-reload
    systemctl enable ovigyan-connector.service
    systemctl restart ovigyan-connector.service
fi

cat <<'MSG'

Ovigyan connector installed and running.
Next, connect it to your Ovigyan site:   sudo ovigyan-connector open
(If this is a server without a screen, forward the port to your PC first:
   ssh -L 47890:127.0.0.1:47890 you@this-server
 then open the link that command prints.)
MSG
