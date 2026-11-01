#!/usr/bin/env python3
# pyright: reportMissingImports=false
"""Nexo Session CLI - Bridge local Hermes terminal sessions to Nexo Appliance."""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

# Add server/src to sys.path if not installed as package
repo_root = Path(__file__).resolve().parent.parent
server_src = repo_root / "server/src"
if server_src.exists() and str(server_src) not in sys.path:
    sys.path.insert(0, str(server_src))

try:
    from nexo_server.session import (
        DEFAULT_SERVER_URL,
        NexoSessionClient,
        find_latest_hermes_session,
        export_hermes_transcript,
    )
except ImportError:
    try:
        from nexo_server.session import (
            DEFAULT_SERVER_URL,
            NexoSessionClient,
            find_latest_hermes_session,
            export_hermes_transcript,
        )
    except ImportError:
        print("Error: Module nexo_server.session not found. Ensure PYTHONPATH includes server/src.")
        sys.exit(1)


def create_client(args: argparse.Namespace) -> NexoSessionClient:
    url = getattr(args, "url", None) or os.getenv("NEXO_SERVER_URL") or DEFAULT_SERVER_URL
    session = getattr(args, "session", None) or os.getenv("NEXO_SESSION_NAME") or None
    profile = getattr(args, "profile", None) or os.getenv("HERMES_PROFILE")
    hermes_bin = getattr(args, "hermes_binary", None) or os.getenv("HERMES_BINARY") or "hermes"
    return NexoSessionClient(
        server_url=url,
        hermes_binary=hermes_bin,
        default_session=session,
        profile=profile,
    )


async def cmd_send(args: argparse.Namespace):
    client = create_client(args)
    message = " ".join(args.message) if isinstance(args.message, list) else str(args.message)
    if not message.strip():
        print("Pesan tidak boleh kosong.")
        return 1

    res = await client.send(message, speak=args.speak, session_name=args.session)
    reply = res.get("reply", "")
    mode = res.get("mode", "server")
    device_spoken = res.get("device_spoken", False)
    expr = res.get("expression")

    print(f"\n🤖 Nexo: {reply}")
    if expr:
        print(f"   [Ekspresi Mascot: {expr}]")
    if device_spoken:
        print("   [Audio: Berhasil disuarakan di layar/speaker appliance 🔊]")
    elif args.speak:
        print("   [Audio: Appliance belum tersambung; respons teks ditampilkan]")
    if mode == "standalone":
        print("   [Mode: Standalone CLI direct]")
    return 0


async def cmd_watch(args: argparse.Namespace):
    client = create_client(args)
    st = await client.get_status()

    online = st.get("server_online", False)
    appliance = "Terhubung 🟢" if st.get("appliance_connected") else "Tidak Terhubung ⚪"
    session_name = st.get("session_thread", client.default_session)

    print("═" * 65)
    print("      NEXO COGNITIVE STREAM & REAL-TIME TRANSCRIPT")
    print(f"  Server: {client.server_url} ({'ONLINE 🟢' if online else 'OFFLINE 🔴'})")
    print(f"  Appliance: {appliance} | Session Thread: {session_name}")
    print("  Menyimak stream kognitif... (Tekan Ctrl+C untuk berhenti)")
    print("═" * 65 + "\n")

    def on_event(event: dict):
        etype = event.get("type")
        iso = event.get("iso_time", "")
        t_short = iso[11:19] if len(iso) >= 19 else ""
        prefix = f"[{t_short}] " if t_short else ""

        if etype == "transcription":
            role = event.get("role", "")
            text = event.get("text", "")
            src = event.get("source", "")
            expr = event.get("expression")
            expr_tag = f" (Mascot: {expr})" if expr else ""
            if role == "user":
                tag = "👤 USER (Suara Appliance)" if src == "appliance_voice" else "👤 USER (Terminal)"
                print(f"{prefix}{tag}: {text}")
            elif role == "assistant":
                print(f"{prefix}🤖 NEXO: {text}{expr_tag}")
        elif etype == "state_change":
            state = event.get("state")
            expr = event.get("expression")
            msg = event.get("message")
            expr_tag = f" | Mascot: {expr}" if expr else ""
            msg_tag = f" ({msg})" if msg else ""
            print(f"{prefix}⚡ STATE: {state}{expr_tag}{msg_tag}")
        elif etype == "cognitive":
            stage = event.get("stage", "thought")
            content = event.get("content", "")
            print(f"{prefix}🧠 COGNITIVE ({stage}): {content}")
        elif etype == "appliance_connected":
            dev = event.get("device_id", "appliance")
            print(f"{prefix}🟢 Appliance terhubung: {dev}")
        elif etype == "appliance_disconnected":
            print(f"{prefix}⚪ Appliance terputus")
        elif etype == "telemetry" and getattr(args, "telemetry", False):
            amp = event.get("amplitude", 0.0)
            bars = "█" * int(amp * 20)
            print(f"{prefix}📊 Level Suara: {bars:<20} ({amp:.2f})")

    stop_event = asyncio.Event()
    try:
        await client.stream_events(on_event, stop_event=stop_event)
    except KeyboardInterrupt:
        print("\nBerhenti menyimak stream.")
    finally:
        stop_event.set()
    return 0


async def cmd_transcript(args: argparse.Namespace):
    client = create_client(args)
    limit = getattr(args, "limit", 20) or 20
    transcripts = await client.get_transcript(limit=limit, session_name=args.session)

    if getattr(args, "json", False):
        print(json.dumps(transcripts, indent=2))
        return 0

    if not transcripts:
        print(f"Belum ada rekaman transkrip untuk sesi '{args.session or client.default_session}'.")
        return 0

    print(f"\n─── Transkrip Sesi Nexo ({len(transcripts)} pesan terakhir) ───")
    for t in transcripts:
        role = t.get("role", "unknown")
        text = t.get("text", "")
        expr = t.get("expression")
        expr_tag = f" [Mascot: {expr}]" if expr else ""
        if role == "user":
            print(f"\n👤 USER: {text}")
        elif role == "assistant":
            print(f"🤖 NEXO: {text}{expr_tag}")
        else:
            print(f"{role.upper()}: {text}")
    print("────────────────────────────────────────────────────────\n")
    return 0


async def cmd_link(args: argparse.Namespace):
    client = create_client(args)
    if getattr(args, "latest", False) or getattr(args, "current", False):
        res = await client.link_latest()
        if res.get("status") == "ok":
            print(f"Berhasil mengaitkan sesi Nexo ke thread Hermes terbaru: '{res.get('session_name')}'")
            return 0
        else:
            print(f"Gagal mengaitkan sesi: {res.get('message', 'Tidak diketahui')}")
            return 1

    target = args.session_name
    if not target:
        # Show current link
        st = await client.get_status()
        current = st.get("session_thread", client.default_session)
        print(f"Session thread Hermes yang saat ini terkait ke Nexo: '{current}'")
        return 0

    res = await client.link(target)
    print(f"Berhasil mengaitkan sesi Nexo ke thread Hermes: '{res.get('session_name', target)}'")
    return 0


async def cmd_speak(args: argparse.Namespace):
    client = create_client(args)
    text = " ".join(args.text) if isinstance(args.text, list) else str(args.text)
    if not text.strip():
        print("Teks ucapan tidak boleh kosong.")
        return 1

    res = await client.speak(text, expression=args.expression)
    if res.get("status") == "ok" and res.get("spoken"):
        print(f"Berhasil memicu audio di appliance: \"{text}\"")
        if args.expression:
            print(f"Ekspresi Mascot: {args.expression}")
        return 0
    else:
        msg = res.get("message", "Appliance tidak terhubung atau gagal memutar audio.")
        print(f"Peringatan: {msg}")
        return 1


async def cmd_bridge(args: argparse.Namespace):
    client = create_client(args)
    await client.interactive_bridge(speak=args.speak, session_name=args.session)
    return 0


async def cmd_status(args: argparse.Namespace):
    client = create_client(args)
    st = await client.get_status()

    online = st.get("server_online", False)
    appliance = st.get("appliance_connected", False)
    state = st.get("state", "UNKNOWN")
    thread = st.get("session_thread", client.default_session)
    dev_id = st.get("device_id") or "—"
    subs = st.get("active_subscribers", 0)

    print("\n═════════════════════════════════════════════════════")
    print("          NEXO APPLIANCE & SESSION STATUS")
    print("═════════════════════════════════════════════════════")
    print(f"  Server URL:         {client.server_url}")
    print(f"  Status Server:      {'ONLINE 🟢' if online else 'OFFLINE 🔴'}")
    print(f"  Status Appliance:   {'TERHUBUNG 🟢' if appliance else 'TIDAK TERHUBUNG ⚪'}")
    print(f"  Device ID:          {dev_id}")
    print(f"  State Appliance:    {state}")
    print(f"  Hermes Session:     {thread}")
    print(f"  Active Subscribers: {subs}")
    print("═════════════════════════════════════════════════════\n")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument("--url", default=None, help=f"URL server Nexo (default: {DEFAULT_SERVER_URL})")
    parent.add_argument("-c", "--session", default=None, help="Nama session thread Hermes (default: nexo)")
    parent.add_argument("-p", "--profile", default=None, help="Hermes profile name")
    parent.add_argument("--hermes-binary", default=None, help="Path binary hermes (default: hermes)")

    parser = argparse.ArgumentParser(
        prog="nexo session",
        description="Utilitas bridging terminal lokal Hermes ke sesi Nexo Appliance.",
        parents=[parent],
    )

    subparsers = parser.add_subparsers(dest="command", help="Perintah sesi")

    # send
    p_send = subparsers.add_parser("send", parents=[parent], help="Kirim pesan/instruksi teks ke sesi Nexo")
    p_send.add_argument("message", nargs="+", help="Teks pesan yang dikirim")
    p_send.add_argument("-s", "--speak", action="store_true", help="Picu output audio di layar/speaker appliance")
    p_send.set_defaults(func=cmd_send)

    # watch / stream
    p_watch = subparsers.add_parser("watch", aliases=["stream", "listen"], parents=[parent], help="Membaca transcript & cognitive stream real-time")
    p_watch.add_argument("--telemetry", action="store_true", help="Tampilkan visual telemetry audio (level suara)")
    p_watch.set_defaults(func=cmd_watch)

    # transcript
    p_trans = subparsers.add_parser("transcript", aliases=["history"], parents=[parent], help="Membaca riwayat transkrip sesi")
    p_trans.add_argument("-n", "--limit", type=int, default=20, help="Jumlah entri transkrip (default: 20)")
    p_trans.add_argument("--json", action="store_true", help="Output dalam format raw JSON")
    p_trans.set_defaults(func=cmd_transcript)

    # link
    p_link = subparsers.add_parser("link", parents=[parent], help="Kaitkan session thread Hermes dengan Nexo")
    p_link.add_argument("session_name", nargs="?", default=None, help="Nama sesi Hermes yang ingin dikaitkan")
    p_link.add_argument("--latest", "--current", action="store_true", dest="latest", help="Deteksi dan kaitkan ke sesi Hermes aktif terbaru")
    p_link.set_defaults(func=cmd_link)

    # speak
    p_speak = subparsers.add_parser("speak", parents=[parent], help="Picu output ucapan audio di appliance")
    p_speak.add_argument("text", nargs="+", help="Teks yang akan diucapkan")
    p_speak.add_argument("-e", "--expression", default=None, help="Ekspresi maskot saat berucap (senang, netral, dll.)")
    p_speak.set_defaults(func=cmd_speak)

    # bridge
    p_bridge = subparsers.add_parser("bridge", parents=[parent], help="Mode bridge terminal interaktif dua arah")
    p_bridge.add_argument("-s", "--speak", action="store_true", help="Aktifkan respons audio appliance secara default")
    p_bridge.set_defaults(func=cmd_bridge)

    # status
    p_status = subparsers.add_parser("status", parents=[parent], help="Lihat status koneksi appliance dan session bridge")
    p_status.set_defaults(func=cmd_status)

    return parser


def main():
    # If invoked as 'nexo session ...' where 'session' is the first arg, strip it
    args_list = sys.argv[1:]
    if args_list and args_list[0] == "session":
        args_list = args_list[1:]

    parser = build_parser()
    if not args_list:
        parser.print_help()
        sys.exit(0)

    args = parser.parse_args(args_list)
    if not hasattr(args, "func"):
        parser.print_help()
        sys.exit(0)

    try:
        rc = asyncio.run(args.func(args))
        sys.exit(rc or 0)
    except KeyboardInterrupt:
        print("\nDibatalkan.")
        sys.exit(130)


if __name__ == "__main__":
    main()
