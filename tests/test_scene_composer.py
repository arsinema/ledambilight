import time
import unittest

import numpy as np

from ledapp.effects import EFFECTS, render_effect
from ledapp.scene_engine import (
    NotificationBus,
    SceneRenderer,
    active_composition,
    blend,
    compositions_from_config,
)
from ledapp.scene_model import (
    Composition,
    Layer,
    default_compositions,
    normalize_compositions,
)


class FakeAudio:
    def __init__(self):
        self.volume = 0.6
        self.beat = 0.8
        self.bass = 0.7
        self.mid = 0.4
        self.treble = 0.3
        self.spectrum = [0.2] * 32


class SceneModelTests(unittest.TestCase):
    def test_defaults_validate(self):
        comps = normalize_compositions(default_compositions())
        self.assertGreaterEqual(len(comps), 4)
        for comp in comps:
            self.assertTrue(comp.id)
            self.assertTrue(comp.layers)
            for layer in comp.layers:
                self.assertIn(layer.type, (
                    "screen", "audio", "effect", "notification",
                    "spotify", "solid", "gradient",
                ))
                self.assertGreaterEqual(layer.opacity, 0.0)
                self.assertLessEqual(layer.opacity, 1.0)

    def test_roundtrip(self):
        comp = Composition(
            id="my scene", name="Тест",
            layers=[Layer(id="l1", type="solid", params={"color": [1, 2, 3]})],
        )
        data = comp.to_dict()
        again = Composition.from_dict(data)
        self.assertEqual(again.id, "my-scene")
        self.assertEqual(again.layers[0].params["color"], [1, 2, 3])
        self.assertEqual(again.to_dict()["layers"][0]["type"], "solid")

    def test_invalid_normalized(self):
        comp = Composition.from_dict({"layers": [{"type": "bad", "opacity": 99,
                                                   "blend": "bad"}]})
        self.assertEqual(comp.layers[0].type, "effect")
        self.assertEqual(comp.layers[0].opacity, 1.0)
        self.assertEqual(comp.layers[0].blend, "normal")


class EffectTests(unittest.TestCase):
    def test_every_effect_returns_shape(self):
        audio = FakeAudio()
        for name in EFFECTS:
            out = render_effect(name, 12, 1.25, {"colors": [[255, 0, 0], [0, 0, 255]]},
                                audio)
            self.assertEqual(out.shape, (12, 3), name)
            self.assertTrue(np.all(out >= 0), name)
            self.assertTrue(np.all(out <= 255), name)

    def test_effect_deterministic(self):
        a = render_effect("plasma", 10, 2.0, {}, None)
        b = render_effect("plasma", 10, 2.0, {}, None)
        self.assertTrue(np.allclose(a, b))

    def test_audio_bars_reacts(self):
        audio = FakeAudio()
        audio.spectrum = [0.0] * 16 + [1.0] * 16
        out = render_effect("audio_bars", 16, 0, {}, audio)
        self.assertGreater(float(out[8:].mean()), float(out[:8].mean()))

    def test_unknown_effect_falls_back(self):
        out = render_effect("nope", 4, 0, {}, None)
        self.assertEqual(out.shape, (4, 3))


class BlendTests(unittest.TestCase):
    def test_modes(self):
        base = np.array([[100, 100, 100]], dtype=np.float32)
        top = np.array([[200, 50, 0]], dtype=np.float32)
        self.assertTrue(np.allclose(blend(base, top, "normal", 1.0), top))
        self.assertTrue(np.allclose(blend(base, top, "add", 1.0), [[255, 150, 100]]))
        self.assertTrue(np.allclose(blend(base, top, "multiply", 1.0),
                                    [[100 * 200 / 255, 100 * 50 / 255, 0]]))

    def test_array_opacity_mask(self):
        base = np.array([[255, 0, 0], [255, 0, 0]], dtype=np.float32)
        top = np.array([[0, 0, 255], [0, 0, 255]], dtype=np.float32)
        weights = np.array([1.0, 0.0], dtype=np.float32)
        out = blend(base, top, "normal", weights)
        self.assertEqual(tuple(out[0].astype(int)), (0, 0, 255))
        self.assertEqual(tuple(out[1].astype(int)), (255, 0, 0))


class RendererTests(unittest.TestCase):
    def test_screen_layer_and_mask(self):
        comp = Composition(
            id="test", name="test",
            layers=[
                Layer(id="screen", type="screen", effect="screen", z=0),
                Layer(id="range", type="solid", z=1, opacity=1.0,
                      params={"color": [0, 255, 0]},
                      mask={"mode": "range", "start": 2, "end": 4}),
            ],
        )
        screen = np.tile(np.array([[255, 0, 0]], dtype=np.float32), (6, 1))
        out = SceneRenderer().render(comp, screen_pixels=screen, led_count=6)
        self.assertEqual(tuple(out[0].astype(int)), (255, 0, 0))
        self.assertEqual(tuple(out[2].astype(int)), (0, 255, 0))
        self.assertEqual(tuple(out[5].astype(int)), (255, 0, 0))

    def test_notification_layer(self):
        bus = NotificationBus()
        bus.push("test", color=(0, 255, 255), duration=5)
        comp = Composition(
            id="n", name="n",
            layers=[
                Layer(id="base", type="solid", params={"color": [0, 0, 0]}),
                Layer(id="note", type="notification", effect="notification",
                      params={"duration": 5}),
            ],
        )
        out = SceneRenderer(bus).render(comp, led_count=4, now=time.monotonic())
        self.assertGreater(float(out[:, 2].mean()), 0)
        bus.clear()
        out = SceneRenderer(bus).render(comp, led_count=4, now=time.monotonic())
        self.assertEqual(float(out.max()), 0.0)

    def test_transition_starts_from_previous(self):
        renderer = SceneRenderer()
        a = Composition(id="a", layers=[Layer(id="a", type="solid",
                                               params={"color": [255, 0, 0]})])
        b = Composition(id="b", layers=[Layer(id="b", type="solid",
                                               params={"color": [0, 0, 255]})],
                        transition_ms=1000)
        first = renderer.render(a, led_count=2, now=0.0)
        self.assertEqual(tuple(first[0].astype(int)), (255, 0, 0))
        second = renderer.render(b, led_count=2, now=0.05)
        self.assertGreater(float(second[0, 0]), 0)
        self.assertLess(float(second[0, 2]), 255)

    def test_config_helpers(self):
        data = {"scene_composer": {"enabled": False, "compositions": default_compositions()}}
        self.assertIsNone(active_composition(data))
        self.assertGreaterEqual(len(compositions_from_config(data)), 4)
        data["scene_composer"]["enabled"] = True
        data["scene_composer"]["active"] = "calm"
        self.assertEqual(active_composition(data).id, "calm")


if __name__ == "__main__":
    unittest.main()
