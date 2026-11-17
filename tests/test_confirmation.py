"""Exercise the actual scan method with fake capture/OCR and no Windows or network."""
import ast
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock
from alert_guard import AlertGuard

source = ast.parse((Path(__file__).parents[1] / 'hatch_watcher.py').read_text())
app = next(n for n in source.body if isinstance(n, ast.ClassDef) and n.name == 'App')
method = next(n for n in app.body if isinstance(n, ast.FunctionDef) and n.name == '_check')


class ConfirmationTests(unittest.TestCase):
    def run_scan(self, second_hit=True, missing=False, last_txt=0, low_cpu=False):
        thread = Mock()
        scope = dict(time=NS(time=lambda: 100, monotonic=lambda: 100),
                     win32gui=NS(IsWindow=lambda _: True, IsIconic=lambda _: False),
                     threading=NS(Thread=thread), play_sound=Mock())
        exec(compile(ast.Module(body=[method], type_ignores=[]), '<scan>', 'exec'), scope)
        st = NS(hwnd=1, watch=True, last_peek=0, thumb_t=100, last_txt=last_txt, last_full=100,
                dc_streak=0, alert_guard=AlertGuard(), label='Account 1')
        first, second = NS(size=10), NS(size=10)
        grabber = NS(capture=Mock(side_effect=[first, None if missing else second]))
        watcher = NS(states={1: st}, running=True, stop=NS(wait=Mock(return_value=False)),
                     analyze=Mock(side_effect=[(True,'reveal',''), (second_hit,'reveal','')]),
                     log=Mock(), record_hatch=Mock(return_value={}), _send_alert=Mock())
        settings = dict(low_cpu=low_cpu, minimized=True, fullsize=False, peek=5, dc_warn=False, rearm=12, sound_alert=False)
        scope['_check'](watcher, st, grabber, settings)
        return watcher, st, grabber, thread, second

    def test_low_cpu_waits_six_seconds_before_capture(self):
        watcher, _, grabber, thread, _ = self.run_scan(last_txt=95, low_cpu=True)
        grabber.capture.assert_not_called()
        watcher.analyze.assert_not_called()
        thread.assert_not_called()

    def test_low_cpu_still_confirms_immediately_when_due(self):
        watcher, _, grabber, thread, _ = self.run_scan(last_txt=90, low_cpu=True)
        self.assertEqual(grabber.capture.call_count, 2)
        watcher.stop.wait.assert_called_once_with(.35)
        thread.assert_called_once()

    def test_no_capture_or_ocr_before_next_scan_is_due(self):
        watcher, st, grabber, thread, _ = self.run_scan(last_txt=99)
        grabber.capture.assert_not_called()
        watcher.analyze.assert_not_called()
        thread.assert_not_called()

    def test_confirms_and_dispatches_before_next_account(self):
        watcher, st, grabber, thread, fresh = self.run_scan()
        self.assertEqual(grabber.capture.call_count, 2)
        watcher.stop.wait.assert_called_once_with(.35)
        watcher.record_hatch.assert_called_once()
        self.assertIs(thread.call_args.kwargs['args'][1], fresh)
        self.assertFalse(st.alert_guard.armed)

    def test_second_frame_must_match(self):
        watcher, st, _, thread, _ = self.run_scan(False)
        thread.assert_not_called()
        watcher.record_hatch.assert_not_called()
        self.assertFalse(st.hit)

    def test_missing_confirmation_never_sends(self):
        watcher, st, _, thread, _ = self.run_scan(missing=True)
        thread.assert_not_called()
        self.assertEqual(st.alert_guard.hits, 0)
        self.assertFalse(st.hit)
