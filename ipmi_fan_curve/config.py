"""Unified configuration persistence — single config.json file."""
import json
import os
from pathlib import Path

from ipmi_fan_curve.models import FanCurve

DATA_DIR = Path(os.environ.get("DATA_DIR", "."))
CONFIG_FILE = DATA_DIR / "config.json"



def _load() -> dict:
    if CONFIG_FILE.exists():
        return json.loads(CONFIG_FILE.read_text())
    # Migrate from old separate files
    data = {}
    if data:
        _save(data)
    return data


def _save(data: dict):
    CONFIG_FILE.write_text(json.dumps(data, indent=2))


def load_curves() -> list[FanCurve]:
    return [FanCurve(**c) for c in _load().get("curves", [])]


def save_curves(curves: list[FanCurve]):
    data = _load()
    data["curves"] = [c.model_dump() for c in curves]
    _save(data)


def load_fan_names() -> dict[str, str]:
    return _load().get("fan_names", {})


def save_fan_names(names: dict[str, str]):
    data = _load()
    data["fan_names"] = names
    _save(data)


def load_fan_overrides() -> dict[str, int]:
    return _load().get("fan_overrides", {})


def save_fan_overrides(overrides: dict[str, int]):
    data = _load()
    data["fan_overrides"] = overrides
    _save(data)
