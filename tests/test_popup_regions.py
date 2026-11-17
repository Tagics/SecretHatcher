"""Verify the actual analyzer reads the full-width banner and isolated button."""
import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import unittest
from alert_guard import reveal_reason


class ImageStub:
    size = (802, 598)
    def crop(self, box):
        return box


tree = ast.parse((Path(__file__).parents[1] / 'hatch_watcher.py').read_text())
app = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'App')
method = next(n for n in app.body if isinstance(n, ast.FunctionDef) and n.name == 'analyze')
scope = dict(Image=SimpleNamespace(fromarray=lambda _: ImageStub()), reveal_reason=reveal_reason)
exec(compile(ast.Module(body=[method], type_ignores=[]), '<analyzer>', 'exec'), scope)


class PopupTests(unittest.TestCase):
    def test_full_banner_not_clipped(self):
        watcher = SimpleNamespace(_ocr_text=Mock(return_value="You've hatched something special..."))
        hit, reason, _ = scope['analyze'](watcher, object())
        self.assertTrue(hit)
        self.assertEqual(reason, 'Special hatch popup')
        box = watcher._ocr_text.call_args.args[0]
        self.assertEqual((box[0], box[2], box[3]), (0, 802, 598))
        watcher._ocr_text.assert_called_once()

    def test_button_fallback(self):
        watcher = SimpleNamespace(_ocr_text=Mock(side_effect=['', 'Hold to Reveal!']))
        self.assertTrue(scope['analyze'](watcher, object())[0])
        self.assertEqual(watcher._ocr_text.call_count, 2)

    def test_dark_scene_is_not_a_hatch(self):
        watcher = SimpleNamespace(_ocr_text=Mock(side_effect=['Collection', 'Island Portals', 'Auto Delete']))
        self.assertFalse(scope['analyze'](watcher, object())[0])
