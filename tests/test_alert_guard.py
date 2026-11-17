import unittest
from alert_guard import AlertGuard, is_reveal_text


class DetectionTests(unittest.TestCase):
    def test_prompt(self):
        self.assertTrue(is_reveal_text('HOLD TO REVEAL!'))
        self.assertTrue(is_reveal_text('H0ld to Reveal'))
        for text in ('dark cave', 'reveal', 'hold your item then reveal it', 'to reveal'):
            self.assertFalse(is_reveal_text(text))

    def test_special_popup_banner(self):
        self.assertTrue(is_reveal_text("You've hatched something special..."))
        self.assertTrue(is_reveal_text("You’ve HATCHED something\nspecial!"))
        self.assertTrue(is_reveal_text("Hoid to Reveal!"))
        for text in ("something special", "you hatched a puppy", "dark forest", "hold to open", "special offer"):
            self.assertFalse(is_reveal_text(text))

    def test_confirmation_and_persistent_screen(self):
        g = AlertGuard()
        self.assertFalse(g.observe(True, 0))
        self.assertTrue(g.observe(True, 3))
        for now in range(6, 300, 3):
            self.assertFalse(g.observe(True, now))

    def test_flicker_and_cooldown(self):
        g = AlertGuard()
        g.observe(True, 0)
        self.assertTrue(g.observe(True, 3))
        g.observe(False, 6)
        g.observe(False, 21)
        self.assertFalse(g.observe(True, 24))
        self.assertFalse(g.observe(True, 27))
        self.assertTrue(g.observe(True, 63))

    def test_failed_reads_cannot_rearm(self):
        g = AlertGuard()
        g.observe(True, 0)
        g.observe(True, 3)
        g.observe(False, 10)
        g.observe(None, 30)
        g.observe(False, 60)
        self.assertFalse(g.observe(True, 63))
        self.assertFalse(g.observe(True, 66))

    def test_negative_breaks_confirmation(self):
        g = AlertGuard()
        g.observe(True, 0)
        g.observe(False, 3)
        self.assertFalse(g.observe(True, 6))
        g.observe(None, 9)
        self.assertFalse(g.observe(True, 12))
        self.assertTrue(g.observe(True, 15))


if __name__ == '__main__':
    unittest.main()
