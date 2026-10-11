"""Offline tools and explicit-port META operations. No vendor DLLs."""
import argparse
import json
from pathlib import Path
from . import commands
from .framing import decode, encode


def main():
    parser = argparse.ArgumentParser(description="Brax SN Writer: AP META tools; limited verified BaseOS profile")
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("status", help="show per-function implementation status")
    sub.add_parser("ports", help="list serial ports; does not open them")
    probe = sub.add_parser("probe", help="read META identity and PRODUCT_INFO serial field")
    probe.add_argument("--port", required=True)
    boot = sub.add_parser("enter-meta", help="negotiate on an explicitly selected preloader COM port")
    boot.add_argument("--port", required=True)
    meta = sub.add_parser("boot-meta", help="reboot the selected BaseOS phone and verify its META endpoint")
    meta.add_argument("--adb", required=True)
    meta.add_argument("--serial", required=True)
    meta.add_argument("--usb-location", required=True)
    normal = sub.add_parser('normal-boot', help='leave META and verify normal boot')
    normal.add_argument('--port', required=True)
    normal.add_argument('--serial', required=True)
    normal.add_argument('--adb', required=True)
    for action in ('imei-read', 'imei-rewrite-existing', 'imei-write-assigned', 'imei-restore'):
        item = sub.add_parser(action)
        item.add_argument('--port', required=True)
        item.add_argument('--serial', required=True)
        if action != 'imei-read':
            item.add_argument('--backup', required=True)
        if action in ('imei-rewrite-existing', 'imei-write-assigned'):
            item.add_argument('--imei', action='append', required=True, help='record 1 then optional record 2')
        if action == 'imei-write-assigned':
            item.add_argument('--initialize-blank', action='store_true')
    change = sub.add_parser("serial-change", help="write one verified BaseOS serial after backup")
    change.add_argument("--port", required=True)
    change.add_argument("--expected", required=True)
    change.add_argument("--new", required=True)
    change.add_argument("--backup", required=True)
    change.add_argument("--initialize-blank", action="store_true")
    restore = sub.add_parser("serial-restore", help="restore the exact pre-test PRODUCT_INFO record")
    restore.add_argument("--port", required=True)
    restore.add_argument("--backup", required=True)
    packet = sub.add_parser("packet", help="print a read-only query packet; sends nothing")
    packet.add_argument("command", choices=("alive", "version", "property"))
    packet.add_argument("--token", type=int, default=1)
    packet.add_argument("--name", help="property name for the property query")
    parse = sub.add_parser("decode", help="decode one captured AP packet; prints only lengths and command header")
    parse.add_argument("--direction", choices=("request", "response"), required=True)
    parse.add_argument("--hex", required=True)
    args = parser.parse_args()
    try:
        if args.action == "status":
            print((Path(__file__).resolve().parent.parent / "capabilities.json").read_text())
        elif args.action == "ports":
            from serial.tools import list_ports
            print(json.dumps([{"port": p.device, "vid": p.vid, "pid": p.pid,
                               "location": p.location, "description": p.description}
                              for p in list_ports.comports()], indent=2))
        elif args.action == "boot-meta":
            from .device_connection import boot_meta
            boot_meta(args.adb, args.serial, args.usb_location,
                      report=lambda event: print(json.dumps(event), flush=True))
        elif args.action == 'normal-boot':
            from .device_connection import return_normal
            print(json.dumps(return_normal(args.adb,args.port,args.serial,
                report=lambda event: print(json.dumps(event),flush=True)),indent=2))
        elif args.action.startswith('imei-'):
            from .session import open_serial
            from .modem import ModemSession
            from .imei import inspect_imeis, rewrite_existing, write_assigned, restore_imeis
            with open_serial(args.port) as stream:
                session = ModemSession(stream,timeout=20)
                if args.action == 'imei-read':
                    identity,records,values=inspect_imeis(session,args.serial)
                    result={'identity':identity,'records':values}
                elif args.action == 'imei-rewrite-existing':
                    result=rewrite_existing(session,args.serial,args.imei,args.backup)
                elif args.action == 'imei-write-assigned':
                    result=write_assigned(session,args.serial,args.imei,args.backup,initialize_blank=args.initialize_blank)
                else:
                    result=restore_imeis(session,args.serial,args.backup)
            print(json.dumps(result,indent=2))
        elif args.action in ("probe", "enter-meta", "serial-change", "serial-restore"):
            from .session import open_serial, MetaSession
            from .serial_record import change_serial, restore_serial, PRODUCT_LID, BASEOS_FINGERPRINT
            with open_serial(args.port) as stream:
                if args.action == "enter-meta":
                    from .meta_boot import enter_meta
                    result = {"handshake": enter_meta(stream), "kernel_endpoint_verified": False}
                else:
                    session = MetaSession(stream)
                    if args.action == "serial-change":
                        result = change_serial(session, args.expected, args.new, args.backup,
                                               initialize_blank=args.initialize_blank)
                    elif args.action == "serial-restore":
                        restore_serial(session, args.backup)
                        result = {"record_restored": True, "reboot_persistence_verified": False}
                    else:
                        import hashlib
                        fingerprint = session.property("ro.build.fingerprint")
                        if fingerprint != BASEOS_FINGERPRINT:
                            raise ValueError("NVRAM probe profile does not match target firmware")
                        serial = session.property("ro.vendor.serialno")
                        data = session.read_record(PRODUCT_LID)
                        if len(data) != 1024:
                            raise ValueError("unexpected PRODUCT_INFO size")
                        result = {"fingerprint": fingerprint, "serial": serial,
                                  "record_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                                  "serial_field_hex": data[116:136].hex()}
                print(json.dumps(result, indent=2))
        elif args.action == "packet":
            if args.command == "property":
                if args.name is None:
                    parser.error("property query requires --name")
                frame = commands.build_property(args.token, args.name)
            else:
                if args.name is not None:
                    parser.error("--name is only valid for property queries")
                frame = getattr(commands, args.command)(args.token)
            print(encode(frame, direction="request").hex())
        else:
            frame = decode(bytes.fromhex(args.hex), direction=args.direction)
            import struct
            if len(frame.local) < 4:
                raise ValueError("AP command header is missing")
            token, command = struct.unpack_from("<HH", frame.local)
            print(json.dumps({"token": token, "command": command,
                              "local_bytes": len(frame.local), "peer_bytes": len(frame.peer)}))
    except (ValueError, OSError, RuntimeError, ImportError) as exc:
        parser.exit(2, f"Error: {exc}\n")


if __name__ == "__main__":
    main()
