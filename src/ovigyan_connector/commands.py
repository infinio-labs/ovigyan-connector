"""The command line for a paired connector: `ovigyan-connector pair | add-device | devices | status | run`."""
from __future__ import annotations

import argparse
import signal
import sys
import threading

from .cloud import CloudError
from .devices import ProbeError
from .service import ConnectorService, CycleReport
from .state import StateStore

COMMANDS = {"pair", "unpair", "add-device", "devices", "remove-device", "status", "run"}

_STATE_WORDS = {
    "ok": "working",
    "unreachable": "cannot reach terminal",
    "pending": "waiting for approval",
    "rejected": "declined",
    "disabled": "switched off",
    "unknown": "not checked yet",
}


def _parser() -> argparse.ArgumentParser:
    # --home is accepted before or after the command.
    home = argparse.ArgumentParser(add_help=False)
    home.add_argument("--home", default=argparse.SUPPRESS, help="folder for the connector's settings (default: the system location)")
    parser = argparse.ArgumentParser(prog="ovigyan-connector", description="Ovigyan attendance connector", parents=[home])
    sub = parser.add_subparsers(dest="command", required=True, parser_class=lambda **kw: argparse.ArgumentParser(parents=[home], **kw))
    pair = sub.add_parser("pair", help="connect this PC to your Ovigyan site with a key from Settings > Connectors")
    pair.add_argument("--server", help="web address of your Ovigyan site")
    pair.add_argument("--key", help="connection key (OVG-XXXXX-XXXXX-XXXXX-XXXXX)")
    sub.add_parser("unpair", help="forget the connection (terminals stay in the list)")
    add = sub.add_parser("add-device", help="add an attendance terminal by IP address")
    add.add_argument("--host", required=True, help="the terminal's IP address")
    add.add_argument("--port", type=int, default=4370)
    add.add_argument("--password", type=int, default=0, help="the terminal's communication password (0 = none)")
    sub.add_parser("devices", help="list terminals and their status")
    remove = sub.add_parser("remove-device", help="remove a terminal from this PC")
    remove.add_argument("id", help="the terminal's id, from `devices`")
    sub.add_parser("status", help="show whether the connector is working")
    run = sub.add_parser("run", help="poll the terminals and deliver punches (use --once for a single pass)")
    run.add_argument("--once", action="store_true")
    return parser


def _print_report(report: CycleReport) -> None:
    print(f"Connector: {report.connector} - {report.message}")
    for device in report.devices:
        label = f"{device.model or 'Terminal'} {device.serial or '?'} ({device.host}:{device.port})"
        print(f"  {label}: {_STATE_WORDS.get(device.state, device.state)} - {device.message}"
              + (f" [sent {device.sent}, queued {device.queued}]" if device.state == "ok" else ""))


def run_command(argv: list[str], service: ConnectorService | None = None) -> int:
    args = _parser().parse_args(argv)
    home_dir = getattr(args, "home", None)
    service = service or ConnectorService(StateStore(home_dir) if home_dir else None)
    try:
        if args.command == "pair":
            server = args.server or input("Web address of your Ovigyan site: ")
            key = args.key or input("Connection key (from Settings > Connectors): ")
            state = service.pair(server, key)
            print(f"Paired as \"{state.connector_name}\" with {state.server}.")
            print("Next: add your terminals with  ovigyan-connector add-device --host <terminal IP>")
        elif args.command == "unpair":
            service.unpair()
            print("Disconnected. Pair again with a new key to resume.")
        elif args.command == "add-device":
            device, existing = service.add_device(args.host, args.port, args.password)
            print(("Updated" if existing else "Added") + f" {device.model or 'terminal'} {device.serial} at {device.host}:{device.port}.")
            print("An administrator now approves it in Ovigyan under Settings > Connectors.")
        elif args.command == "devices":
            status = service.status()
            last = {d["id"]: d for d in (status.get("lastCycle") or {}).get("devices", [])}
            if not status["devices"]:
                print("No terminals yet. Add one with  ovigyan-connector add-device --host <IP>")
            for device in status["devices"]:
                info = last.get(device["id"])
                word = _STATE_WORDS.get(info["state"], info["state"]) if info else "not checked yet"
                print(f"{device['id']}  {device['model'] or 'Terminal'} {device['serial']}  {device['host']}:{device['port']}  {word}")
        elif args.command == "remove-device":
            if not service.remove_device(args.id):
                print("No terminal with that id.", file=sys.stderr)
                return 1
            print("Removed.")
        elif args.command == "status":
            status = service.status()
            print(f"Ovigyan connector {status['version']}")
            print("Paired with " + f"{status['server']} as \"{status['connectorName']}\"" if status["paired"] else f"Not paired ({status['status']}).")
            for device in status["devices"]:
                print(f"  {device['model'] or 'Terminal'} {device['serial']} at {device['host']}:{device['port']}")
            last = status.get("lastCycle")
            if last:
                print(f"Last check {last['at']}: {last['connector']} - {last['message']}")
        elif args.command == "run":
            if args.once:
                report = service.run_cycle()
                _print_report(report)
                return 0 if report.connector in {"ok"} else 1
            stop = threading.Event()
            for name in ("SIGINT", "SIGTERM"):
                if hasattr(signal, name):
                    signal.signal(getattr(signal, name), lambda *_: stop.set())
            print("Ovigyan connector running. Press Ctrl+C to stop.")
            service.run_forever(stop)
        return 0
    except (CloudError, ProbeError) as error:
        print(error.message, file=sys.stderr)
        return 1
