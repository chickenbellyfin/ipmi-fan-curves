"""Pydantic models for curve configuration."""
from pydantic import BaseModel


class CurvePoint(BaseModel):
    temp: float
    pct: float


class FanCurve(BaseModel):
    id: str
    name: str
    sensors: list[str]
    aggregation: str
    fans: list[str]
    points: list[CurvePoint]


class CurvesPayload(BaseModel):
    curves: list[FanCurve]


class FanNamesPayload(BaseModel):
    names: dict[str, str]


class FanOverridesPayload(BaseModel):
    overrides: dict[str, int]
