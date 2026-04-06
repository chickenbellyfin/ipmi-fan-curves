"""Mock IPMI backend — simulated sensors for local development.

No real IPMI commands. Temps drift via sine waves, fan RPMs are random.
"""
import asyncio
import logging
import math
import os
import random
import time

log = logging.getLogger("ipmi-fan")

POLL_INTERVAL = 10
METRICS_CONTENT_TYPE = "text/plain"

_TEMP_BASES = {
    "CPU Temp": (48, 12),
    "PCH Temp": (52, 8),
    "System Temp": (36, 6),
    "Peripheral Temp": (40, 5),
    "VRM Temp": (50, 10),
    "DIMM Temp": (42, 7),
    "NVMe Temp": (55, 15),
}

_FAN_BASES = {"FAN1": 1200, "FAN2": 1100, "FAN3": 900, "FANA": 1400, "FANB": 800}

_cached_sensors: list[dict] = []


def get_cached_sensors() -> list[dict]:
    return _cached_sensors


def metrics_output() -> bytes:
    return b""


def _poll_tick():
    global _cached_sensors
    t = time.time()
    sensors = []
    for name, (center, amp) in _TEMP_BASES.items():
        phase = hash(name) % 100
        reading = center + math.sin(t / 30 + phase) * amp * 0.5 + random.uniform(-1.5, 1.5)
        reading = round(max(10.0, min(95.0, reading)), 1)
        sensors.append({"id": name, "name": name, "reading": reading, "unit": "C"})
    for name, base_rpm in _FAN_BASES.items():
        rpm = max(0, base_rpm + random.randint(-50, 50))
        sensors.append({"id": name, "name": name, "reading": rpm, "unit": "RPM"})
    _cached_sensors = sensors


async def start():
    log.info("[MOCK] Running with simulated sensors")
    interval = int(os.environ.get("POLL_INTERVAL", POLL_INTERVAL))
    while True:
        _poll_tick()
        await asyncio.sleep(interval)
