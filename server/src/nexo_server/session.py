"""Terminal & Local Session Bridge for Nexo Appliance."""

import asyncio
import json
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
from typing import Any, Callable, Coroutine, Dict, List, Optional
import urllib.error
import urllib.parse
import urllib.request

try:
    import websockets
except ImportError:
    websockets = None  # type: ignore

logger = logging.getLogger(__name__)

DEFAULT_SERVER_URL = os.getenv("NEXO_SERVER_URL") or os.getenv("JARVIS_SERVER_URL") or "http://127.0.0.1:8765"


def _get_session_link_path() -> Path:
    nexo_dir = Path(os.getenv("NEXO_CONFIG_DIR") or "/root/.config/nexo-auth")
    if nexo_dir.exists():
        return nexo_dir / "session_link.json"
    jarvis_dir = Path(os.getenv("JARVIS_CONFIG_DIR") or "/root/.config/jarvis-auth")
    if jarvis_dir.exists():
        return jarvis_dir / "session_link.json"
    return nexo_dir / "session_link.json"


def get_persisted_session(default: str = "nexo") -> str:
    env_val = os.getenv("NEXO_SESSION_NAME") or os.getenv("JARVIS_SESSION_NAME")
    if env_val and env_val.strip():
        return env_val.strip()
    link_path = _get_session_link_path()
    if link_path.exists():
        try:
            data = json.loads(link_path.read_text(encoding="utf-8"))
            val = data.get("session_name")
            if val and isinstance(val, str) and val.strip():
                return val.strip()
        except Exception:
            pass
    peer_path = Path("/root/.config/jarvis-auth/session_link.json")
    if peer_path.exists() and peer_path != link_path:
        try:
            data = json.loads(peer_path.read_text(encoding="utf-8"))
            val = data.get("session_name")
            if val and isinstance(val, str) and val.strip():
                return val.strip()
        except Exception:
            pass
    return default


def persist_session_link(session_name: str) -> None:
    link_path = _get_session_link_path()
    try:
        link_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        payload = {
            "session_name": session_name.strip(),
            "updated_at": int(time.time() * 1000),
        }
        link_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except Exception as e:
        logger.debug("Failed to persist session link: %s", e)


def find_latest_hermes_session(
    hermes_binary: str = "hermes",
    profile: Optional[str] = None,
) -> Optional[str]:
    """Discover the most recently active Hermes session ID or title."""
    bin_path = shutil.which(hermes_binary) or hermes_binary
    cmd = [bin_path]
    if profile:
        cmd.extend(["-p", profile])
    cmd.extend(["sessions", "list"])

    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=10,
            check=False,
        )
        if proc.returncode != 0:
            # Try with default profile
            if not profile:
                cmd = [bin_path, "-p", "default", "sessions", "list"]
                proc = subprocess.run(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=10,
                    check=False,
                )
        if proc.returncode != 0:
            return None

        lines = proc.stdout.strip().split("\n")
        # Find session lines after header separator
        found_separator = False
        for line in lines:
            if "───" in line:
                found_separator = True
                continue
            if found_separator and line.strip():
                parts = re.split(r"\s{2,}", line.strip())
                if len(parts) >= 1:
                    title = parts[0].strip()
                    last_id = parts[-1].strip() if len(parts) >= 2 else title
                    return title if title and title != "—" else last_id
    except Exception as e:
        logger.debug("Failed to list Hermes sessions: %s", e)
    return None


def resolve_session_id(
    session_name_or_id: str,
    hermes_binary: str = "hermes",
    profile: Optional[str] = None,
) -> tuple[str, Optional[str]]:
    """Resolve session title or alias to canonical (session_id, profile)."""
    if session_name_or_id.startswith("local-") or (
        len(session_name_or_id) >= 32 and bool(re.fullmatch(r"[0-9a-fA-F-]+", session_name_or_id))
    ):
        return session_name_or_id, profile

    bin_path = shutil.which(hermes_binary) or hermes_binary
    env_profile = os.getenv("HERMES_PROFILE")
    profiles_to_try = [profile] if profile else [p for p in (env_profile, None, "default") if p is not None or p is None]
    # deduplicate while preserving order
    seen = set()
    deduped_profiles = []
    for p in profiles_to_try:
        if p not in seen:
            seen.add(p)
            deduped_profiles.append(p)

    for prof in deduped_profiles:
        cmd = [bin_path]
        if prof:
            cmd.extend(["-p", prof])
        cmd.extend(["sessions", "list"])
        try:
            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=10,
                check=False,
            )
            if proc.returncode == 0:
                for line in proc.stdout.split("\n"):
                    if "───" in line or not line.strip():
                        continue
                    parts = re.split(r"\s{2,}", line.strip())
                    if len(parts) >= 2:
                        title = parts[0].strip()
                        session_id = parts[-1].strip()
                        if (
                            title.lower() == session_name_or_id.lower()
                            or session_name_or_id.lower() in title.lower()
                            or session_id.lower() == session_name_or_id.lower()
                        ):
                            return session_id, prof
        except Exception:
            pass
    return session_name_or_id, profile


def export_hermes_transcript(
    session_id_or_title: str,
    limit: int = 50,
    hermes_binary: str = "hermes",
    profile: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Export past conversation messages from Hermes session store."""
    resolved_id, resolved_profile = resolve_session_id(session_id_or_title, hermes_binary, profile)
    bin_path = shutil.which(hermes_binary) or hermes_binary

    env_profile = os.getenv("HERMES_PROFILE")
    profiles_to_try = [resolved_profile] if resolved_profile else [env_profile, None, "default"]
    seen = set()
    deduped_profiles = []
    for p in profiles_to_try:
        if p not in seen:
            seen.add(p)
            deduped_profiles.append(p)

    for prof in deduped_profiles:
        cmd = [bin_path]
        if prof:
            cmd.extend(["-p", prof])
        
        # Try by session-id first if it looks like an ID, else try title
        if resolved_id.startswith("local-") or len(resolved_id) >= 20:
            cmd.extend(["sessions", "export", "--session-id", resolved_id, "-"])
        else:
            cmd.extend(["sessions", "export", "--title", resolved_id, "-"])

        try:
            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=15,
                check=False,
            )
            if proc.returncode != 0:
                continue

            raw_output = proc.stdout.strip()
            if not raw_output:
                continue

            transcripts: List[Dict[str, Any]] = []
            for line in raw_output.split("\n"):
                line = line.strip()
                if not line or not line.startswith("{"):
                    continue
                try:
                    data = json.loads(line)
                    messages = data.get("messages", [])
                    for m in messages:
                        role = m.get("role")
                        content = m.get("content", "")
                        if role in ("user", "assistant") and content:
                            transcripts.append({
                                "type": "transcription",
                                "role": role,
                                "text": str(content),
                                "timestamp": m.get("timestamp"),
                                "source": "hermes_store",
                            })
                except Exception:
                    continue

            if transcripts:
                return transcripts[-limit:]
        except Exception as e:
            logger.debug("Failed to export Hermes transcript: %s", e)

    return []


class NexoSessionClient:
    """Client for bridging local terminal sessions to Nexo Appliance."""

    def __init__(
        self,
        server_url: str = DEFAULT_SERVER_URL,
        hermes_binary: str = "hermes",
        default_session: Optional[str] = None,
        profile: Optional[str] = None,
        timeout: float = 60.0,
    ):
        self.server_url = server_url.rstrip("/")
        self.hermes_binary = hermes_binary
        self.default_session = (default_session.strip() if default_session else "") or get_persisted_session()
        self.profile = profile
        self.timeout = timeout

    @property
    def ws_url(self) -> str:
        url = self.server_url
        if url.startswith("https://"):
            return "wss://" + url[8:]
        elif url.startswith("http://"):
            return "ws://" + url[7:]
        return "ws://" + url

    def _http_request(
        self,
        endpoint: str,
        method: str = "GET",
        data: Optional[Dict[str, Any]] = None,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Perform HTTP request against Nexo Server."""
        url = f"{self.server_url}{endpoint}"
        body_bytes = json.dumps(data).encode("utf-8") if data is not None else None
        headers = {"Content-Type": "application/json"} if body_bytes else {}
        req = urllib.request.Request(url, data=body_bytes, headers=headers, method=method)

        t = timeout or self.timeout
        with urllib.request.urlopen(req, timeout=t) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}

    async def _async_http_request(
        self,
        endpoint: str,
        method: str = "GET",
        data: Optional[Dict[str, Any]] = None,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Perform non-blocking HTTP request using asyncio loop."""
        return await asyncio.to_thread(self._http_request, endpoint, method, data, timeout)

    async def send(
        self,
        message: str,
        speak: bool = False,
        session_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Send a message to the Nexo session."""
        target_session = session_name or self.default_session
        try:
            res = await self._async_http_request(
                "/session/send",
                method="POST",
                data={
                    "message": message,
                    "speak": speak,
                    "session_name": target_session,
                },
                timeout=self.timeout,
            )
            res["mode"] = "server"
            return res
        except (urllib.error.URLError, ConnectionRefusedError, OSError):
            # Standalone fallback: execute directly via Hermes CLI
            logger.info("Nexo server unreachable; invoking Hermes CLI directly in session '%s'", target_session)
            reply = await self._query_hermes_direct(message, session_name=target_session)
            return {
                "status": "ok",
                "reply": reply,
                "spoken": speak,
                "device_spoken": False,
                "session_name": target_session,
                "mode": "standalone",
            }

    async def speak(self, text: str, expression: Optional[str] = None) -> Dict[str, Any]:
        """Trigger voice output on connected appliance."""
        try:
            return await self._async_http_request(
                "/session/speak",
                method="POST",
                data={"text": text, "expression": expression},
                timeout=15.0,
            )
        except Exception as e:
            return {
                "status": "error",
                "message": f"Gagal memicu audio di appliance: {e}",
                "spoken": False,
            }

    async def link(self, session_name: str) -> Dict[str, Any]:
        """Link Hermes session thread (nexo / appliance / active local session) with Nexo."""
        target = session_name.strip()
        self.default_session = target
        persist_session_link(target)
        try:
            res = await self._async_http_request(
                "/session/link",
                method="POST",
                data={"session_name": target},
                timeout=10.0,
            )
            return res
        except Exception:
            return {"status": "ok", "session_name": target, "mode": "client_only"}

    async def link_latest(self) -> Dict[str, Any]:
        """Auto-detect most recently active Hermes session and link it."""
        latest = await asyncio.to_thread(find_latest_hermes_session, self.hermes_binary, self.profile)
        if not latest:
            return {"status": "error", "message": "Tidak dapat mendeteksi sesi Hermes terbaru."}
        return await self.link(latest)

    async def get_status(self) -> Dict[str, Any]:
        """Get live status of appliance and server."""
        try:
            status = await self._async_http_request("/session/status", method="GET", timeout=5.0)
            status["server_online"] = True
            return status
        except Exception:
            return {
                "server_online": False,
                "appliance_connected": False,
                "active_sessions_count": 0,
                "session_thread": self.default_session,
                "state": "DISCONNECTED",
                "hermes_mode": "cli",
                "active_subscribers": 0,
            }

    async def get_transcript(
        self,
        limit: int = 50,
        session_name: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Retrieve recent conversation transcript."""
        target_session = session_name or self.default_session
        try:
            res = await self._async_http_request(
                f"/session/transcript?limit={limit}",
                method="GET",
                timeout=10.0,
            )
            transcripts = res.get("transcript", [])
            if transcripts:
                return transcripts
        except Exception:
            pass

        # Fallback to Hermes session store
        return await asyncio.to_thread(
            export_hermes_transcript,
            target_session,
            limit,
            self.hermes_binary,
            self.profile,
        )

    async def stream_events(
        self,
        on_event: Callable[[Dict[str, Any]], None],
        stop_event: Optional[asyncio.Event] = None,
    ) -> None:
        """Stream cognitive events, state changes, and transcripts via WebSocket."""
        if websockets is None:
            raise RuntimeError("Package 'websockets' is required for streaming. Install it or check environment.")

        endpoint = f"{self.ws_url}/ws/session/stream"
        async with websockets.connect(endpoint) as ws:
            while stop_event is None or not stop_event.is_set():
                try:
                    msg_text = await asyncio.wait_for(ws.recv(), timeout=1.0)
                    if isinstance(msg_text, bytes):
                        msg_text = msg_text.decode("utf-8")
                    event = json.loads(msg_text)
                    on_event(event)
                except asyncio.TimeoutError:
                    continue
                except websockets.exceptions.ConnectionClosed:
                    break

    async def _query_hermes_direct(self, prompt: str, session_name: str) -> str:
        """Execute Hermes chat command directly as a fallback."""
        bin_path = shutil.which(self.hermes_binary) or self.hermes_binary
        cmd = [bin_path]
        if self.profile:
            cmd.extend(["-p", self.profile])
        cmd.extend([
            "chat",
            "-c",
            session_name,
            "--create-if-missing",
            "-q",
            prompt,
            "-Q",
        ])

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=self.timeout)
            if proc.returncode != 0:
                err = stderr.decode().strip()
                logger.error("Hermes CLI error: %s", err)
                return "Terjadi kendala saat memproses instruksi melalui Hermes CLI."

            lines = stdout.decode().strip().split("\n")
            clean_lines = [
                line for line in lines
                if not line.strip().startswith(("↻ Resumed", "Session local-", "Model restored", "session_id:"))
            ]
            clean = "\n".join(clean_lines).strip()
            return clean if clean else stdout.decode().strip()
        except asyncio.TimeoutError:
            return "Waktu pemrosesan melebihi batas (Hermes timeout)."
        except Exception as e:
            return f"Kesalahan internal saat eksekusi Hermes CLI: {e}"

    async def interactive_bridge(
        self,
        speak: bool = False,
        session_name: Optional[str] = None,
    ) -> None:
        """Interactive two-way bridge mode inside the terminal."""
        target_session = session_name or self.default_session
        print("\n" + "═" * 65)
        print("        NEXO TERMINAL & APPLIANCE TWO-WAY BRIDGE")
        print(f"  Target Server: {self.server_url}")
        print(f"  Hermes Thread: {target_session}")
        print("  Perintah: /speak (toggle audio), /link <nama>, /status, /quit")
        print("═" * 65 + "\n")

        stop_event = asyncio.Event()
        current_speak = [speak]

        def on_event(event: Dict[str, Any]):
            event_type = event.get("type")
            if event_type == "transcription":
                role = event.get("role", "unknown")
                text = event.get("text", "")
                src = event.get("source", "")
                if role == "user":
                    tag = "👤 APPLIANCE USER" if src == "appliance_voice" else "👤 USER"
                    print(f"\n[{tag}] {text}")
                elif role == "assistant":
                    expr = event.get("expression")
                    expr_tag = f" (Mascot: {expr})" if expr else ""
                    print(f"[{'🤖 NEXO'}] {text}{expr_tag}")
            elif event_type == "state_change":
                state = event.get("state")
                expr = event.get("expression")
                msg = event.get("message")
                expr_str = f" | Mascot: {expr}" if expr else ""
                msg_str = f" ({msg})" if msg else ""
                if state in ("LISTENING", "THINKING", "SPEAKING", "ERROR"):
                    print(f"⚡ [STATE: {state}{expr_str}]{msg_str}")

        # Stream task in background
        stream_task = None
        if websockets is not None:
            async def run_stream():
                while not stop_event.is_set():
                    try:
                        await self.stream_events(on_event, stop_event=stop_event)
                    except Exception:
                        await asyncio.sleep(2.0)

            stream_task = asyncio.create_task(run_stream())

        try:
            loop = asyncio.get_running_loop()
            while not stop_event.is_set():
                audio_status = "ON 🔊" if current_speak[0] else "OFF 🔇"
                prompt_line = f"[{target_session} | Voice: {audio_status}] > "
                user_input = await loop.run_in_executor(None, input, prompt_line)
                text = user_input.strip()
                if not text:
                    continue

                if text in ("/quit", "/exit", "exit"):
                    break
                elif text == "/speak":
                    current_speak[0] = not current_speak[0]
                    print(f"Audio response appliance diubah: {'AKTIF' if current_speak[0] else 'NONAKTIF'}")
                    continue
                elif text.startswith("/link"):
                    parts = text.split(maxsplit=1)
                    if len(parts) > 1:
                        target_session = parts[1].strip()
                        await self.link(target_session)
                        print(f"Session thread diarahkan ke: {target_session}")
                    else:
                        print(f"Session thread saat ini: {target_session}")
                    continue
                elif text == "/status":
                    st = await self.get_status()
                    print(f"Status: {json.dumps(st, indent=2)}")
                    continue

                # Normal prompt send
                res = await self.send(text, speak=current_speak[0], session_name=target_session)
                reply = res.get("reply", "")
                expr = res.get("expression")
                mode = res.get("mode", "server")
                expr_tag = f" [Mascot: {expr}]" if expr else ""
                device_tag = " (Disuarakan di Appliance 🔊)" if res.get("device_spoken") else ""
                print(f"🤖 NEXO: {reply}{expr_tag}{device_tag}")
                if mode == "standalone":
                    print("   [Mode: Standalone CLI direct]")

        except (KeyboardInterrupt, EOFError):
            print("\nKeluar dari mode bridge.")
        finally:
            stop_event.set()
            if stream_task:
                stream_task.cancel()
                await asyncio.gather(stream_task, return_exceptions=True)


# Dual-compatibility alias
JarvisSessionClient = NexoSessionClient
