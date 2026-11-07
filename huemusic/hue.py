"""Philips Hue: pairing, entertainment areas (CLIP v2 REST) and the
Entertainment API stream (DTLS over UDP, port 2100).

How the Entertainment API works, in short:
  1. Pair with the bridge (press the link button) asking for a `clientkey`.
     You get an app key ("username") and a client key (a 16-byte PSK).
  2. PUT {"action": "start"} on an entertainment configuration (area).
  3. Open a DTLS 1.2 connection with PSK identity = app key, PSK = client key.
  4. Send "HueStream" packets with an RGB value per channel, ~50 times/s.
     The bridge forwards them to the lights over Zigbee at ~25 Hz.
  5. PUT {"action": "stop"} when done (the bridge also times out after ~10 s).
"""
from __future__ import annotations

import socket
import struct
import threading
import time

import requests
import urllib3

from .effects import RGB

# The bridge uses a certificate signed by Signify's own CA, which isn't in the
# system trust store. We talk to it on the LAN only.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

STREAM_PORT = 2100


class HueError(RuntimeError):
    pass


def discover() -> list[str]:
    """Find bridges via Philips' cloud discovery endpoint (needs internet)."""
    try:
        r = requests.get("https://discovery.meethue.com", timeout=5)
        r.raise_for_status()
        return [b["internalipaddress"] for b in r.json()]
    except Exception:
        return []


def pair(ip: str, devicetype: str = "huemusic#pc") -> tuple[str, str] | None:
    """Returns (app_key, client_key), or None if the link button wasn't pressed."""
    r = requests.post(f"https://{ip}/api", json={"devicetype": devicetype, "generateclientkey": True},
                      verify=False, timeout=5)
    data = r.json()[0]
    if "success" in data:
        return data["success"]["username"], data["success"]["clientkey"]
    err = data.get("error", {})
    if err.get("type") == 101:  # link button not pressed
        return None
    raise HueError(err.get("description", str(data)))


class Bridge:
    def __init__(self, ip: str, app_key: str):
        self.ip = ip
        self.session = requests.Session()
        self.session.headers["hue-application-key"] = app_key
        self.session.verify = False

    def _url(self, path: str) -> str:
        return f"https://{self.ip}/clip/v2/resource/{path}"

    def get(self, path: str) -> list[dict]:
        r = self.session.get(self._url(path), timeout=5)
        if r.status_code == 403 or r.status_code == 401:
            raise HueError("bridge rejected the app key: run `huemusic setup-hue` again")
        r.raise_for_status()
        return r.json()["data"]

    def put(self, path: str, body: dict) -> None:
        r = self.session.put(self._url(path), json=body, timeout=5)
        if not r.ok:
            try:
                errors = "; ".join(e["description"] for e in r.json().get("errors", []))
            except Exception:
                errors = r.text
            raise HueError(f"PUT {path} failed ({r.status_code}): {errors}")

    def entertainment_areas(self) -> list[dict]:
        return self.get("entertainment_configuration")

    def channel_list(self, area: dict) -> list[dict]:
        """Channels of an area with friendly names and positions (-1..1)."""
        ent_owner = {e["id"]: e.get("owner", {}).get("rid") for e in self.get("entertainment")}
        dev_name = {d["id"]: d.get("metadata", {}).get("name", "?") for d in self.get("device")}
        per_device: dict[str, int] = {}
        out = []
        for ch in sorted(area.get("channels", []), key=lambda c: c["channel_id"]):
            members = ch.get("members") or []
            name = "?"
            if members:
                dev = ent_owner.get(members[0]["service"]["rid"])
                name = dev_name.get(dev, "?")
                per_device[name] = per_device.get(name, 0) + 1
            pos = ch.get("position", {})
            out.append({"id": ch["channel_id"], "name": name,
                        "x": pos.get("x", 0.0), "y": pos.get("y", 0.0), "z": pos.get("z", 0.0)})
        # Gradient strips expose several channels: number them.
        seen: dict[str, int] = {}
        for c in out:
            if per_device.get(c["name"], 0) > 1:
                seen[c["name"]] = seen.get(c["name"], 0) + 1
                c["name"] = f"{c['name']} #{seen[c['name']]}"
        return out

    def application_id(self) -> str:
        """The PSK identity for the DTLS stream (API v2). Not the same as the app key."""
        r = self.session.get(f"https://{self.ip}/auth/v1", timeout=5)
        app_id = r.headers.get("hue-application-id")
        if not app_id:
            raise HueError(f"bridge didn't return a hue-application-id (HTTP {r.status_code})")
        return app_id

    def light_for_service(self, entertainment_rid: str) -> str:
        """Light resource id behind an entertainment service (entertainment -> device -> light)."""
        ent = self.get(f"entertainment/{entertainment_rid}")[0]
        device = self.get(f"device/{ent['owner']['rid']}")[0]
        for s in device.get("services", []):
            if s.get("rtype") == "light":
                return s["rid"]
        raise HueError("no light found for this channel")

    def identify(self, light_rid: str) -> None:
        """Make the light blink a few times (Hue's built-in identify)."""
        self.put(f"light/{light_rid}", {"identify": {"action": "identify"}})

    def set_positions(self, area: dict, positions: dict[str, tuple[float, float]]) -> tuple[int, list[str]]:
        """Write new (x, y) floor positions for entertainment services of an area.

        `positions` maps entertainment service id -> (x, y). Heights (z) are
        kept. Services with several positions (gradient strips) are left
        unchanged and reported as skipped.
        """
        updated, skipped, out = 0, [], []
        for loc in area.get("locations", {}).get("service_locations", []):
            rid = loc["service"]["rid"]
            pts = loc.get("positions") or [loc.get("position")]
            entry = {"service": loc["service"], "positions": pts}
            if "equalization_factor" in loc:
                entry["equalization_factor"] = loc["equalization_factor"]
            if rid in positions:
                if len(pts) == 1:
                    x, y = positions[rid]
                    entry["positions"] = [{"x": round(x, 5), "y": round(y, 5), "z": pts[0].get("z", 0.0)}]
                    updated += 1
                else:
                    skipped.append(rid)
            out.append(entry)
        self.put(f"entertainment_configuration/{area['id']}", {"locations": {"service_locations": out}})
        return updated, skipped

    def set_streaming(self, area_id: str, active: bool) -> None:
        self.put(f"entertainment_configuration/{area_id}", {"action": "start" if active else "stop"})


def area_name(area: dict) -> str:
    return area.get("metadata", {}).get("name") or area.get("name") or area["id"]


def build_message(area_id: str, frame: dict[int, RGB], brightness: float = 1.0, seq: int = 0) -> bytes:
    """HueStream v2 packet: 16-byte header, 36-byte area id, then 7 bytes per channel."""
    header = b"HueStream" + bytes([
        0x02, 0x00,   # protocol version 2.0
        seq & 0xFF,   # sequence number (ignored by the bridge)
        0x00, 0x00,   # reserved
        0x00,         # color space: RGB
        0x00,         # reserved
    ]) + area_id.encode("ascii")
    body = b"".join(
        struct.pack(">BHHH", cid, *(int(max(0.0, min(1.0, v * brightness)) * 65535) for v in rgb))
        for cid, rgb in list(frame.items())[:20]  # max 20 channels per message
    )
    return header + body


class _RetransmitSocket(socket.socket):
    """UDP socket remembering the last flight of datagrams so it can resend it."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._flight: list[bytes] = []
        self._received = True

    def send(self, data, *args):
        if self._received:  # first send after a receive starts a new flight
            self._flight, self._received = [], False
        self._flight.append(bytes(data))
        return super().send(data, *args)

    def recv(self, *args):
        data = super().recv(*args)
        self._received = True
        return data

    def retransmit(self) -> None:
        for d in self._flight:
            super().send(d)


class EntertainmentStream:
    def __init__(self, ip: str, app_key: str, client_key: str, area_id: str):
        self.ip, self.app_key, self.client_key, self.area_id = ip, app_key, client_key, area_id
        self.bridge = Bridge(ip, app_key)
        self.sock = None
        self.seq = 0

    def open(self, timeout: float = 10.0) -> None:
        from mbedtls import tls  # import here so preview mode works without it

        identity = self.bridge.application_id()
        self.bridge.set_streaming(self.area_id, True)
        conf = tls.DTLSConfiguration(
            pre_shared_key=(identity, bytes.fromhex(self.client_key)),
            ciphers=["TLS-PSK-WITH-AES-128-GCM-SHA256"],
            lowest_supported_version=tls.DTLSVersion.DTLSv1_2,
            highest_supported_version=tls.DTLSVersion.DTLSv1_2,
            validate_certificates=False,
            handshake_timeout_max=timeout,
        )
        raw = _RetransmitSocket(socket.AF_INET, socket.SOCK_DGRAM)
        sock = tls.ClientContext(conf).wrap_socket(raw, server_hostname=None)

        # UDP can lose packets, and the bridge reliably ignores the first
        # ClientHello-with-cookie. DTLS clients must retransmit their last
        # flight when no answer comes, but python-mbedtls just blocks on
        # recv(). So: short socket timeout, and on timeout we resend the last
        # flight ourselves, until `timeout` expires.
        error: list[BaseException] = []
        deadline = time.monotonic() + timeout

        def handshake() -> None:
            try:
                sock.connect((self.ip, STREAM_PORT))
                raw.settimeout(0.5)
                while True:
                    try:
                        sock.do_handshake()
                        return
                    except (TimeoutError, socket.timeout):
                        if time.monotonic() > deadline:
                            raise
                        raw.retransmit()
            except BaseException as e:
                error.append(e)

        th = threading.Thread(target=handshake, daemon=True)
        th.start()
        th.join(timeout + 1)
        if th.is_alive() or error:
            reason = f" (no answer within {timeout:.0f} s)" if th.is_alive() or isinstance(
                error[0], (TimeoutError, socket.timeout)) else f": {error[0]}"
            try:
                sock.close()
            except Exception:
                pass
            self.bridge.set_streaming(self.area_id, False)
            raise HueError(
                f"DTLS handshake with the bridge failed{reason}. Check that no other app "
                "(Hue Sync, Hue app 'Sync' tab) is streaming to this area, then retry; "
                "if it persists run `setup-hue --repair`."
            )
        self.sock = sock

    def send(self, frame: dict[int, RGB], brightness: float = 1.0) -> None:
        if self.sock is None:
            return
        self.seq += 1
        self.sock.send(build_message(self.area_id, frame, brightness, self.seq))

    def close(self) -> None:
        if self.sock is not None:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None
            time.sleep(0.1)
        try:
            self.bridge.set_streaming(self.area_id, False)
        except Exception:
            pass
