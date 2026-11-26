import unittest

from ledapp.automation import (
    AutomationEngine,
    Rule,
    default_rules,
    normalize_rules,
)


class AutomationTests(unittest.TestCase):
    def _config(self, rules, enabled=True):
        return {"automation": {"enabled": enabled, "rules": [r.to_dict() for r in rules]}}

    def test_rule_normalization(self):
        rule = Rule.from_dict({
            "id": "r 1", "name": "Test",
            "trigger": {"type": "process", "value": "cs2"},
            "actions": [{"type": "set_scene", "scene": "Игра"}],
        })
        self.assertEqual(rule.id, "r-1")
        self.assertEqual(rule.trigger["value"], "cs2")
        self.assertEqual(rule.actions[0]["scene"], "Игра")

    def test_process_trigger_and_action(self):
        rule = Rule(id="r", trigger={"type": "process", "value": "cs2"},
                    actions=[{"type": "brightness", "delta": 10}], cooldown=0)
        engine = AutomationEngine(self._config([rule]))
        matches = engine.handle("process", {"process": "CS2.exe"}, {}, now=100)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].action["delta"], 10)
        self.assertEqual(engine.handle("process", {"process": "opera.exe"}, {}, now=101), [])

    def test_cooldown(self):
        rule = Rule(id="r", trigger={"type": "process", "value": "cs2"},
                    actions=[{"type": "notification"}], cooldown=10)
        engine = AutomationEngine(self._config([rule]))
        self.assertEqual(len(engine.handle("process", {"process": "cs2"}, {}, now=100)), 1)
        self.assertEqual(engine.handle("process", {"process": "cs2"}, {}, now=105), [])
        self.assertEqual(len(engine.handle("process", {"process": "cs2"}, {}, now=111)), 1)

    def test_disabled_engine(self):
        rule = Rule(id="r", trigger={"type": "process", "value": "cs2"},
                    actions=[{"type": "notification"}], cooldown=0)
        engine = AutomationEngine(self._config([rule], enabled=False))
        self.assertEqual(engine.handle("process", {"process": "cs2"}, {}, now=100), [])

    def test_priority_order(self):
        low = Rule(id="low", trigger={"type": "process", "value": "cs2"},
                   actions=[{"type": "set_scene", "scene": "Ночь"}],
                   cooldown=0, priority=0)
        high = Rule(id="high", trigger={"type": "process", "value": "cs2"},
                    actions=[{"type": "set_scene", "scene": "Игра"}],
                    cooldown=0, priority=10)
        engine = AutomationEngine(self._config([low, high]))
        matches = engine.handle("process", {"process": "cs2"}, {}, now=100)
        self.assertEqual(matches[0].rule.id, "high")

    def test_title_and_audio_triggers(self):
        title_rule = Rule(id="t", trigger={"type": "window_title", "value": "youtube"},
                          actions=[{"type": "set_scene", "scene": "Кино"}], cooldown=0)
        audio_rule = Rule(id="a", trigger={"type": "audio", "metric": "volume",
                                            "op": ">", "value": 0.4},
                          actions=[{"type": "notification"}], cooldown=0)
        engine = AutomationEngine(self._config([title_rule, audio_rule]))
        self.assertEqual(len(engine.handle("window_title", {"title": "YouTube — video"},
                                          {}, now=1)), 1)
        self.assertEqual(len(engine.handle("window_title", {"title": "Other"},
                                          {}, now=2)), 0)
        self.assertEqual(len(engine.handle("audio", {"volume": 0.8}, {}, now=3)), 1)
        self.assertEqual(len(engine.handle("audio", {"volume": 0.2}, {}, now=4)), 0)

    def test_conditions(self):
        rule = Rule(id="c", trigger={"type": "process", "value": "cs2"},
                    conditions=[{"type": "scene", "value": "ночь"}],
                    actions=[{"type": "notification"}], cooldown=0)
        engine = AutomationEngine(self._config([rule]))
        self.assertEqual(engine.handle("process", {"process": "cs2"},
                                       {"scene": "Игра"}, now=1), [])
        self.assertEqual(len(engine.handle("process", {"process": "cs2"},
                                           {"scene": "Ночь"}, now=2)), 1)

    def test_default_rules_valid(self):
        rules = normalize_rules(default_rules())
        self.assertEqual(len(rules), 2)
        self.assertTrue(rules[0].actions)


if __name__ == "__main__":
    unittest.main()
