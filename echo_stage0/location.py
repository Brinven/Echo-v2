"""
Location / context awareness for Echo (Stage 5 Part 5).

Resolves where Echo is — "home", "jeep", or "unknown" — from a local network
fingerprint, so her persona can shift (house downtime vs Jeep driving companion).
Purely local: reads Michael's own network (default-gateway MAC + a known-host
reachability check). Nothing leaves the machine.

Mirrors daily_state.py: fail-soft, resolved once at session start, with a test seam
(`force` / injected `probe`) so harnesses never touch the real network. Any probe
error resolves to "unknown" → NEUTRAL (home-style) behavior, never "jeep" — doing
Jeep-telemetry talk when location is uncertain is exactly the awkwardness this removes.

No new dependencies: Windows-native probes via subprocess (PowerShell Get-NetRoute /
Get-NetNeighbor, `arp -a` fallback for the MAC, `ping` for the known-host backup).
"""

import re
import json
import subprocess
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).resolve().parent / "echo_location.json"

# Fail-soft defaults: detection on but no home fingerprint → resolves "unknown" (neutral).
_DEFAULT_CONFIG = {
    "location_detection_enabled": True,
    "home_gateway_mac": "",
    "home_known_host": "",
    "recheck_interval_min": 0,
}

# Hard cap so a slow probe never stalls session start. PowerShell cold-start is the
# dominant cost; the probe itself is instant. One-time at startup, so this is generous.
_PROBE_TIMEOUT_S = 2.0
_PING_TIMEOUT_MS = 400

_VALID = ("home", "jeep", "unknown")


def load_location_config() -> dict:
    """Load echo_location.json, falling back to documented defaults on any error."""
    config = dict(_DEFAULT_CONFIG)
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        for key in _DEFAULT_CONFIG:
            if key in data:
                config[key] = data[key]
    except FileNotFoundError:
        logger.info("echo_location.json not found; location detection off → unknown")
        config["location_detection_enabled"] = False
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"echo_location.json unreadable ({e}); location detection off → unknown")
        config["location_detection_enabled"] = False
    return config


def _normalize_mac(mac: str | None) -> str | None:
    """Canonicalize a MAC to lowercase hex, separators stripped ('' / None → None)."""
    if not mac:
        return None
    cleaned = re.sub(r"[^0-9a-fA-F]", "", mac).lower()
    return cleaned or None


def _probe_gateway() -> tuple[str | None, str | None]:
    """Return (gateway_ip, gateway_mac) for the IPv4 default route. (None, None) on failure.

    PowerShell primary (structured output); `arp -a` fallback for the MAC if the neighbor
    lookup came back empty (VPN/adapter quirks). Never raises.
    """
    ps = (
        "$g = (Get-NetRoute -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue "
        "| Sort-Object RouteMetric | Select-Object -First 1).NextHop; "
        "$m = (Get-NetNeighbor -IPAddress $g -ErrorAction SilentlyContinue "
        "| Select-Object -First 1).LinkLayerAddress; "
        "Write-Output \"$g|$m\""
    )
    gw_ip = gw_mac = None
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, text=True, timeout=_PROBE_TIMEOUT_S,
        ).stdout.strip()
        if "|" in out:
            gw_ip, gw_mac = (part.strip() or None for part in out.split("|", 1))
    except (subprocess.SubprocessError, OSError) as e:
        logger.warning(f"gateway probe (PowerShell) failed: {e}")

    if gw_ip and not gw_mac:
        gw_mac = _arp_mac(gw_ip)
    return gw_ip, gw_mac


def _arp_mac(ip: str) -> str | None:
    """Fallback: read a host's MAC from the ARP cache (`arp -a <ip>`). None on failure."""
    try:
        out = subprocess.run(
            ["arp", "-a", ip], capture_output=True, text=True, timeout=_PROBE_TIMEOUT_S
        ).stdout
    except (subprocess.SubprocessError, OSError) as e:
        logger.warning(f"arp fallback failed: {e}")
        return None
    # A MAC on Windows arp output looks like aa-bb-cc-dd-ee-ff.
    m = re.search(r"([0-9a-fA-F]{2}(?:[-:][0-9a-fA-F]{2}){5})", out)
    return m.group(1) if m else None


def _host_reachable(host: str) -> bool:
    """Quick reachability check to a known home host (single ping). Never raises."""
    if not host:
        return False
    try:
        return subprocess.run(
            ["ping", "-n", "1", "-w", str(_PING_TIMEOUT_MS), host],
            capture_output=True, text=True, timeout=_PROBE_TIMEOUT_S,
        ).returncode == 0
    except (subprocess.SubprocessError, OSError) as e:
        logger.warning(f"known-host ping failed: {e}")
        return False


def resolve_location(config: dict | None = None, *, force: str | None = None,
                     probe: tuple[str | None, str | None] | None = None) -> str:
    """Resolve 'home' / 'jeep' / 'unknown' from the local network fingerprint.

    Args:
        config: location config (loaded from echo_location.json if omitted).
        force: test seam — return this value directly (must be a valid location).
        probe: test seam — inject a (gateway_ip, gateway_mac) tuple instead of hitting
            the real network.

    Logic (PRD §4):
        detection disabled OR no home fingerprint configured → "unknown" (neutral)
        gateway MAC == home_gateway_mac  OR  home_known_host reachable → "home"
        probe succeeded but no match → "jeep"
        probe error / no network → "unknown" (fail-soft to neutral, NOT jeep)
    """
    if force is not None:
        return force if force in _VALID else "unknown"

    config = config or load_location_config()
    if not config.get("location_detection_enabled", True):
        return "unknown"

    home_mac = _normalize_mac(config.get("home_gateway_mac", ""))
    home_host = (config.get("home_known_host") or "").strip()
    # No fingerprint at all → can't tell home from jeep → neutral, never guess jeep.
    if not home_mac and not home_host:
        logger.info("location detection on but no home fingerprint configured → unknown")
        return "unknown"

    gw_ip, gw_mac = probe if probe is not None else _probe_gateway()

    if home_mac and _normalize_mac(gw_mac) == home_mac:
        return "home"
    if home_host and _host_reachable(home_host):
        return "home"
    # Probe got a gateway but it isn't home → off the home network → jeep.
    if gw_ip or gw_mac:
        return "jeep"
    # Couldn't probe the network at all → neutral.
    return "unknown"
