import math
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .domain import HANDLE


@dataclass(frozen=True)
class Target:
    name: str
    kind: str
    value: str
    include_replies: bool = True
    poll_seconds: float | None = None

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.value}:{self.include_replies}"


@dataclass(frozen=True)
class Config:
    state_dir: Path
    targets: tuple[Target, ...]
    poll_seconds: float = 15
    timeout_seconds: float = 15
    batch_size: int = 80
    alert_max_age_seconds: int = 900
    alert_on_first_poll: bool = False
    page_size: int = 20
    max_pages: int = 4
    reconcile_seconds: float = 900
    request_spacing_seconds: float = 2

    def interval(self, target: Target) -> float:
        return target.poll_seconds if target.poll_seconds is not None else self.poll_seconds


def load_config(path: Path) -> Config:
    path = path.resolve()
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    allowed = set(Config.__dataclass_fields__)
    if unknown := data.keys() - allowed:
        raise ValueError(f"unknown config options: {sorted(unknown)}")
    targets = []
    raw_targets = data.get("targets", [])
    if not isinstance(raw_targets, list):
        raise ValueError("targets must be a TOML array of tables")
    for item in raw_targets:
        if not isinstance(item, dict) or item.keys() - set(Target.__dataclass_fields__):
            raise ValueError("invalid target options")
        if not all(isinstance(item.get(key), str) and item[key].strip() for key in ("name", "kind", "value")):
            raise ValueError("each target requires name, kind and value strings")
        target = Target(**item)
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", target.name):
            raise ValueError("target name must be 1-64 letters, digits, underscores or hyphens")
        if target.kind not in {"user", "search", "list"}:
            raise ValueError("target kind must be user, search or list")
        if target.kind == "user" and not HANDLE.fullmatch(target.value):
            raise ValueError("user target must be a handle without @")
        if target.kind == "list" and not re.fullmatch(r"[0-9]+", target.value):
            raise ValueError("list target must have a numeric ID")
        if len(target.value) > 1000 or not isinstance(target.include_replies, bool):
            raise ValueError("invalid target value or include_replies")
        if target.poll_seconds is not None and (
            type(target.poll_seconds) not in {int, float}
            or not math.isfinite(target.poll_seconds)
            or not 15 <= target.poll_seconds <= 86400
        ):
            raise ValueError("target poll_seconds must be between 15 and 86400")
        targets.append(target)
    if len({target.name for target in targets}) != len(targets) or len({t.key for t in targets}) != len(targets):
        raise ValueError("duplicate target name or definition")
    state = data.get("state_dir", "state")
    if not isinstance(state, str) or not state.strip():
        raise ValueError("state_dir must be a nonempty path")
    values = {key: value for key, value in data.items() if key not in {"targets", "state_dir"}}
    config = Config(state_dir=(path.parent / state).resolve(), targets=tuple(targets), **values)
    for key, minimum, maximum in (
        ("poll_seconds", 15, 86400),
        ("timeout_seconds", 5, 300),
        ("reconcile_seconds", 60, 86400),
        ("request_spacing_seconds", 1, 300),
    ):
        value = getattr(config, key)
        if type(value) not in {int, float} or not math.isfinite(value) or not minimum <= value <= maximum:
            raise ValueError(f"{key} must be between {minimum} and {maximum}")
    for key, minimum, maximum in (
        ("batch_size", 1, 400),
        ("alert_max_age_seconds", 1, 86400),
        ("page_size", 5, 40),
        ("max_pages", 1, 10),
    ):
        value = getattr(config, key)
        if type(value) is not int or not minimum <= value <= maximum:
            raise ValueError(f"{key} must be an integer between {minimum} and {maximum}")
    if not isinstance(config.alert_on_first_poll, bool):
        raise ValueError("alert_on_first_poll must be true or false")
    return config
