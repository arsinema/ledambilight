"""Automation Studio 2.0: правила «когда → если → тогда».

Движок не трогает GUI/железо: он только решает, какие действия сработали.
Исполнитель живёт в MainWindow и вызывает сцены, композиции, Telegram и т.п.
"""

from __future__ import annotations

import copy
import re
import time
import uuid
from dataclasses import dataclass, field

TRIGGER_TYPES = {
    "process": "Запущено приложение",
    "window_title": "Заголовок окна",
    "game": "Игра",
    "time": "Время",
    "audio": "Громкость звука",
    "spotify": "Spotify",
    "telegram": "Telegram",
    "notification": "Уведомление Windows",
    "wled_lost": "Потеряна связь с WLED",
}
ACTION_TYPES = {
    "set_composition": "Включить композицию",
    "set_scene": "Включить сцену",
    "brightness": "Изменить яркость",
    "notification": "Показать уведомление на ленте",
    "telegram": "Отправить в Telegram",
    "pause": "Пауза захвата",
    "resume": "Продолжить захват",
    "blackout": "Погасить ленту",
}
TRIGGER_FIELDS = {
    "process": {"value": ""},
    "window_title": {"value": ""},
    "game": {"event": "start"},
    "time": {"from": "22:00", "to": "07:00"},
    "audio": {"metric": "volume", "op": ">", "value": 0.35},
    "spotify": {"event": "track", "value": ""},
    "telegram": {"value": ""},
    "notification": {"app": "", "value": ""},
    "wled_lost": {},
}
ACTION_FIELDS = {
    "set_composition": {"composition": ""},
    "set_scene": {"scene": "Игра"},
    "brightness": {"delta": 10},
    "notification": {"text": "Событие", "color": [80, 200, 255], "duration": 2.0},
    "telegram": {"text": "Событие"},
    "pause": {},
    "resume": {},
    "blackout": {},
}


def _clean_id(value):
    text = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(value or "").strip())
    return text.strip("-")[:48] or uuid.uuid4().hex[:12]


@dataclass
class Rule:
    id: str
    name: str = "Новое правило"
    enabled: bool = True
    trigger: dict = field(default_factory=lambda: {"type": "process", "value": ""})
    conditions: list = field(default_factory=list)
    actions: list = field(default_factory=list)
    cooldown: float = 5.0
    last_run: float = 0.0
    priority: int = 0

    def normalized(self):
        self.id = _clean_id(self.id)
        self.name = str(self.name or "Правило")[:80]
        trigger = copy.deepcopy(self.trigger) if isinstance(self.trigger, dict) else {}
        ttype = trigger.get("type", "process")
        if ttype not in TRIGGER_TYPES:
            ttype = "process"
        defaults = TRIGGER_FIELDS.get(ttype, {})
        trigger = {**defaults, **trigger, "type": ttype}
        self.trigger = trigger
        clean_actions = []
        for action in self.actions or []:
            if not isinstance(action, dict):
                continue
            atype = action.get("type", "notification")
            if atype not in ACTION_TYPES:
                continue
            clean_actions.append({**ACTION_FIELDS.get(atype, {}), **action, "type": atype})
        self.actions = clean_actions
        self.conditions = [
            copy.deepcopy(item) for item in self.conditions or []
            if isinstance(item, dict)
        ]
        try:
            self.cooldown = max(0.0, min(3600.0, float(self.cooldown)))
        except (TypeError, ValueError):
            self.cooldown = 5.0
        try:
            self.priority = int(self.priority)
        except (TypeError, ValueError):
            self.priority = 0
        return self

    def to_dict(self):
        self.normalized()
        return {
            "id": self.id, "name": self.name, "enabled": self.enabled,
            "trigger": copy.deepcopy(self.trigger),
            "conditions": copy.deepcopy(self.conditions),
            "actions": copy.deepcopy(self.actions),
            "cooldown": self.cooldown, "priority": self.priority,
        }

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict):
            data = {}
        return cls(
            id=data.get("id") or uuid.uuid4().hex[:12],
            name=data.get("name", "Правило"),
            enabled=bool(data.get("enabled", True)),
            trigger=data.get("trigger") or {},
            conditions=data.get("conditions") or [],
            actions=data.get("actions") or [],
            cooldown=data.get("cooldown", 5.0),
            priority=data.get("priority", 0),
            last_run=data.get("last_run", 0.0),
        ).normalized()


def _as_text(value):
    return str(value or "").lower()


def _time_in_range(now, start, end):
    def parse(value, fallback):
        try:
            hour, minute = str(value).split(":")[:2]
            return int(hour) * 60 + int(minute)
        except Exception:
            return fallback
    start_min = parse(start, 0)
    end_min = parse(end, 24 * 60 - 1)
    current = now.hour * 60 + now.minute
    if start_min <= end_min:
        return start_min <= current <= end_min
    return current >= start_min or current <= end_min


def _match_trigger(rule, event, payload, now):
    trigger = rule.trigger
    ttype = trigger.get("type", "process")
    value = _as_text(trigger.get("value", ""))
    if ttype == "process":
        process = _as_text(payload.get("process"))
        return bool(value) and value in process
    if ttype == "window_title":
        title = _as_text(payload.get("title"))
        return bool(value) and value in title
    if ttype == "game":
        return _as_text(payload.get("event")) == _as_text(trigger.get("event", "start"))
    if ttype == "time":
        return _time_in_range(now, trigger.get("from", "00:00"),
                              trigger.get("to", "23:59"))
    if ttype == "audio":
        metric = trigger.get("metric", "volume")
        try:
            actual = float(payload.get(metric, 0.0) or 0.0)
            wanted = float(trigger.get("value", 0.0) or 0.0)
        except (TypeError, ValueError):
            return False
        op = trigger.get("op", ">")
        if op == ">":
            return actual > wanted
        if op == ">=":
            return actual >= wanted
        if op == "<":
            return actual < wanted
        if op == "<=":
            return actual <= wanted
        return abs(actual - wanted) < 0.05
    if ttype == "spotify":
        event_name = _as_text(trigger.get("event", "track"))
        if _as_text(payload.get("event")) != event_name:
            return False
        return not value or value in _as_text(payload.get("track"))
    if ttype == "telegram":
        return bool(value) and value in _as_text(payload.get("text"))
    if ttype == "notification":
        app = _as_text(trigger.get("app"))
        text = _as_text(payload.get("text"))
        if app and app not in _as_text(payload.get("app")):
            return False
        return not value or value in text
    if ttype == "wled_lost":
        return True
    return False


def _condition_ok(condition, context, now):
    ctype = condition.get("type", "")
    value = _as_text(condition.get("value"))
    if ctype == "process":
        return value in _as_text(context.get("process"))
    if ctype == "title":
        return value in _as_text(context.get("title"))
    if ctype == "composition":
        return value == _as_text(context.get("composition"))
    if ctype == "scene":
        return value == _as_text(context.get("scene"))
    if ctype == "audio":
        try:
            return float(context.get("volume", 0.0)) >= float(condition.get("value", 0.0))
        except (TypeError, ValueError):
            return False
    if ctype == "time":
        return _time_in_range(now, condition.get("from", "00:00"),
                              condition.get("to", "23:59"))
    return True


@dataclass
class RuleMatch:
    rule: Rule
    action: dict


class AutomationEngine:
    def __init__(self, config):
        self.config = config
        self.reload()

    def reload(self):
        section = (self.config or {}).get("automation", {})
        self.rules = [Rule.from_dict(item) for item in section.get("rules") or []]
        self.rules.sort(key=lambda rule: -rule.priority)
        self._last_time_rule = 0.0

    def enabled(self):
        return bool((self.config or {}).get("automation", {}).get("enabled", False))

    def handle(self, event, payload=None, context=None, now=None):
        if not self.enabled():
            return []
        now = time.monotonic() if now is None else float(now)
        import datetime
        wall = datetime.datetime.now()
        payload = payload or {}
        context = context or {}
        matches = []
        for rule in self.rules:
            if not rule.enabled:
                continue
            if now - rule.last_run < rule.cooldown:
                continue
            if not _match_trigger(rule, event, payload, wall):
                continue
            if not all(_condition_ok(item, context, wall) for item in rule.conditions):
                continue
            rule.last_run = now
            for action in rule.actions:
                matches.append(RuleMatch(rule, copy.deepcopy(action)))
        return matches


def default_rules():
    return [
        Rule(
            id="game-start", name="Игра → композиция Игра 2.0",
            trigger={"type": "game", "event": "start"},
            actions=[{"type": "set_composition", "composition": "game-2"}],
            cooldown=3,
        ).to_dict(),
        Rule(
            id="telegram-flash", name="Telegram → вспышка",
            trigger={"type": "notification", "app": "telegram"},
            actions=[{
                "type": "notification", "text": "Telegram",
                "color": [80, 180, 255], "duration": 2.0,
            }],
            cooldown=1,
        ).to_dict(),
    ]


def normalize_rules(items):
    result = []
    seen = set()
    for raw in items or []:
        rule = Rule.from_dict(raw)
        if rule.id in seen:
            rule.id = f"{rule.id}-{uuid.uuid4().hex[:4]}"
        seen.add(rule.id)
        result.append(rule)
    return result
