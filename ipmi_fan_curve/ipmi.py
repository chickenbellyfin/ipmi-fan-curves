"""Real IPMI backend using ipmitool.

Owns the polling loop, sensor cache, and fan control. All ipmitool
subprocess calls happen here — the web server never touches IPMI directly.
"""
import asyncio
import logging
import os
import re
import subprocess
import time
from dataclasses import dataclass, field

from prometheus_client import Gauge, CollectorRegistry, generate_latest, CONTENT_TYPE_LATEST

from ipmi_fan_curve import config
from ipmi_fan_curve.models import FanCurve

log = logging.getLogger("ipmi-fan")

POLL_INTERVAL = 30
MIN_DUTY = int(os.environ.get("MIN_DUTY", 20))

# ── IPMI command profiles ──────────────────────────────────────────────────

@dataclass
class IpmiProfile:
    name: str
    manual_enable: list[str]
    manual_disable: list[str]
    set_duty_args: list[str]   # use {zone} and {duty} placeholders
    zones: dict[str, str] = field(default_factory=lambda: {"cpu": "0x00", "peripheral": "0x01"})

    def fan_zone(self, fan: str) -> str:
        if fan.upper().startswith("FAN") and fan[-1].isalpha():
            return self.zones["peripheral"]
        return self.zones["cpu"]

    def set_duty_cmd(self, fan: str, pct: int) -> list[str]:
        zone = self.fan_zone(fan)
        duty = format(max(0, min(100, pct)), "02x")
        return [s.format(zone=zone, duty=duty) for s in self.set_duty_args]


PROFILES: dict[str, IpmiProfile] = {
    "supermicro-classic": IpmiProfile(
        name="SuperMicro Classic (X9/X10/X11)",
        manual_enable=["raw", "0x30", "0x30", "0x01", "0x00"],
        manual_disable=["raw", "0x30", "0x30", "0x01", "0x01"],
        set_duty_args=["raw", "0x30", "0x30", "0x02", "{zone}", "0x{duty}"],
    ),
    "supermicro-h12": IpmiProfile(
        name="SuperMicro H12/H13 (AMD EPYC)",
        manual_enable=["raw", "0x30", "0x45", "0x01", "0x01"],
        manual_disable=["raw", "0x30", "0x45", "0x01", "0x00"],
        set_duty_args=["raw", "0x30", "0x70", "0x66", "0x01", "{zone}", "0x{duty}"],
    ),
}

_active_profile: IpmiProfile | None = None


def _detect_profile() -> IpmiProfile:
    """Try each profile's manual_enable command; return the first that succeeds."""
    for key, profile in PROFILES.items():
        cmd = ["ipmitool"] + profile.manual_enable
        log.info("Probing profile %s: %s", key, " ".join(cmd))
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            log.info("Detected IPMI profile: %s (%s)", key, profile.name)
            return profile
        log.debug("Profile %s failed: %s", key, result.stderr.strip())
    raise RuntimeError(
        "No IPMI profile matched this BMC. Use --ipmi-profile to specify one, "
        f"or add a new profile. Available: {', '.join(PROFILES)}"
    )


def set_profile(name: str | None):
    """Set the active profile by name, or auto-detect if None."""
    global _active_profile
    if name:
        if name not in PROFILES:
            raise ValueError(f"Unknown IPMI profile '{name}'. Available: {', '.join(PROFILES)}")
        _active_profile = PROFILES[name]
        log.info("Using IPMI profile: %s (%s)", name, _active_profile.name)
    else:
        _active_profile = _detect_profile()

# ── Sensor cache ────────────────────────────────────────────────────────────

_cached_sensors: list[dict] = []

def get_cached_sensors() -> list[dict]:
    return _cached_sensors

# ── Prometheus metrics ──────────────────────────────────────────────────────

registry = CollectorRegistry()

TEMP_GAUGE = Gauge(
    "ipmi_temperature_celsius",
    "Temperature sensor reading",
    ["sensor"],
    registry=registry,
)
FAN_SPEED_GAUGE = Gauge(
    "ipmi_fan_speed_rpm",
    "Fan speed in RPM",
    ["fan"],
    registry=registry,
)
FAN_DUTY_GAUGE = Gauge(
    "ipmi_fan_duty_percent",
    "Computed fan duty cycle from curve",
    ["curve", "fan"],
    registry=registry,
)
CURVE_TEMP_GAUGE = Gauge(
    "ipmi_curve_temperature_celsius",
    "Aggregated input temperature for a curve",
    ["curve"],
    registry=registry,
)

_last_successful_poll: float | None = None

POLL_AGE_GAUGE = Gauge(
    "ipmi_poll_age_seconds",
    "Seconds since last successful sensor poll",
    registry=registry,
)
POLL_AGE_GAUGE.set_function(
    lambda: time.time() - _last_successful_poll if _last_successful_poll is not None else float("inf")
)

def metrics_output() -> bytes:
    return generate_latest(registry)

METRICS_CONTENT_TYPE = CONTENT_TYPE_LATEST

# ── Interpolation & aggregation ─────────────────────────────────────────────

def interpolate(points: list[dict], temp: float) -> float:
    """Linear interpolation across curve points. Returns fan %."""
    if not points:
        return 100.0
    pts = sorted(points, key=lambda p: p["temp"])
    if temp <= pts[0]["temp"]:
        return pts[0]["pct"]
    if temp >= pts[-1]["temp"]:
        return pts[-1]["pct"]
    for i in range(len(pts) - 1):
        if pts[i]["temp"] <= temp <= pts[i + 1]["temp"]:
            t0, p0 = pts[i]["temp"], pts[i]["pct"]
            t1, p1 = pts[i + 1]["temp"], pts[i + 1]["pct"]
            ratio = (temp - t0) / (t1 - t0) if t1 != t0 else 0
            return p0 + ratio * (p1 - p0)
    return 100.0


def compute_curve_duty(curve: FanCurve, sensor_map: dict[str, float]) -> tuple[float, float] | None:
    """Aggregate sensor readings and interpolate fan %. Returns (temp, pct) or None."""
    readings = [sensor_map[sid] for sid in curve.sensors if sid in sensor_map]
    if not readings:
        return None
    if curve.aggregation == "max":
        temp = max(readings)
    else:
        temp = sum(readings) / len(readings)
    points = [p.model_dump() for p in curve.points]
    return temp, interpolate(points, temp)

# ── ipmitool helpers ────────────────────────────────────────────────────────

def _ipmi_raw(args: list[str]) -> str:
    """Run ipmitool with given args, return stdout."""
    cmd = ["ipmitool"] + args
    log.debug("Running: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    if result.returncode != 0:
        log.error("ipmitool error: %s", result.stderr.strip())
        raise RuntimeError(result.stderr.strip())
    return result.stdout


def _discover_sensors() -> list[dict]:
    """Return list of {id, name, reading, unit} from `ipmitool sdr list`."""
    out = _ipmi_raw(["sdr", "list", "full"])
    sensors = []
    for line in out.strip().splitlines():
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 3:
            continue
        name = parts[0]
        reading_str = parts[1]
        m = re.match(r"([\d.]+)\s*(degrees C|RPM)", reading_str)
        if not m:
            continue
        value = float(m.group(1))
        unit = "C" if "degrees C" in m.group(2) else "RPM"
        sensors.append({"id": name, "name": name, "reading": value, "unit": unit})
    return sensors


def _enable_manual_mode():
    """Enable manual fan control using the active profile."""
    _ipmi_raw(_active_profile.manual_enable)
    log.info("Enabled manual fan control (%s)", _active_profile.name)


# ── Fan threshold management ─────────────────────────────────────────────

# How often to re-apply fan thresholds (seconds). BMC can reset them on
# certain events, so we periodically enforce them.  Default 6 hours.
_THRESH_INTERVAL = int(os.environ.get("FAN_THRESH_INTERVAL", 6 * 3600))
_last_thresh_apply: float = 0.0

# Lower thresholds for fan RPM sensors (LNR, LC, LNC).
# Desktop fans spin slower than server fans, so the BMC's default Lower
# Critical threshold triggers false "fan failure" alarms that override
# manual duty control.  These values suppress that.
_FAN_THRESH_LNR = 0
_FAN_THRESH_LC = 0
_FAN_THRESH_LNC = 0


def _lower_fan_thresholds():
    """Discover fan RPM sensors and set their lower thresholds low enough
    to prevent the BMC from overriding manual fan control."""
    global _last_thresh_apply
    sensors = _discover_sensors()
    for s in sensors:
        if s["unit"] != "RPM":
            continue
        fan = s["name"]
        try:
            _ipmi_raw([
                "sensor", "thresh", fan, "lower",
                str(_FAN_THRESH_LNR), str(_FAN_THRESH_LC), str(_FAN_THRESH_LNC),
            ])
            log.info("Set fan thresholds for %s: LNR=%d LC=%d LNC=%d",
                     fan, _FAN_THRESH_LNR, _FAN_THRESH_LC, _FAN_THRESH_LNC)
        except Exception as e:
            log.warning("Failed to set thresholds for %s: %s", fan, e)
    _last_thresh_apply = time.time()


def _set_fan_speed(fan: str, pct: int):
    """Set fan duty cycle using the active profile."""
    pct = max(MIN_DUTY, min(100, pct))
    try:
        _ipmi_raw(_active_profile.set_duty_cmd(fan, pct))
        log.info("Set fan %s to %d%% (zone %s)", fan, pct, _active_profile.fan_zone(fan))
    except Exception as e:
        log.error("Failed to set fan speed for %s: %s", fan, e)

# ── Polling loop ────────────────────────────────────────────────────────────

def _poll_tick():
    """Run one IPMI poll cycle (blocking). Called from a thread."""
    global _cached_sensors, _last_successful_poll

    if _THRESH_INTERVAL > 0 and (time.time() - _last_thresh_apply) >= _THRESH_INTERVAL:
        _lower_fan_thresholds()

    sensors = _discover_sensors()
    _cached_sensors = sensors
    _last_successful_poll = time.time()

    sensor_map = {s["id"]: s["reading"] for s in sensors}
    curves = config.load_curves()
    overrides = config.load_fan_overrides()

    for s in sensors:
        if s["unit"] == "C":
            TEMP_GAUGE.labels(sensor=s["name"]).set(s["reading"])
        elif s["unit"] == "RPM":
            FAN_SPEED_GAUGE.labels(fan=s["name"]).set(s["reading"])

    for curve in curves:
        result = compute_curve_duty(curve, sensor_map)
        if result is None:
            continue
        temp, fan_pct = result
        log.info("Curve %s: temp=%.1fC -> fan=%.0f%%", curve.name, temp, fan_pct)

        CURVE_TEMP_GAUGE.labels(curve=curve.name).set(round(temp, 1))
        for fan in curve.fans:
            if fan in overrides:
                continue
            FAN_DUTY_GAUGE.labels(curve=curve.name, fan=fan).set(round(fan_pct, 1))
            _set_fan_speed(fan, int(fan_pct))

    for fan, pct in overrides.items():
        FAN_DUTY_GAUGE.labels(curve="OVERRIDE", fan=fan).set(pct)
        _set_fan_speed(fan, int(pct))


async def start():
    """Enable manual mode and start the polling loop. Call from app lifespan."""
    if _active_profile is None:
        await asyncio.to_thread(set_profile, None)
    await asyncio.to_thread(_enable_manual_mode)
    await asyncio.to_thread(_lower_fan_thresholds)
    interval = int(os.environ.get("POLL_INTERVAL", POLL_INTERVAL))
    while True:
        try:
            await asyncio.to_thread(_poll_tick)
        except Exception as e:
            log.error("Control loop error: %s", e)
        await asyncio.sleep(interval)
