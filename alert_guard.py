"""Platform-independent confirmation and duplicate-alert protection."""
import re


def reveal_reason(text):
    text = re.sub(r"[^a-z0-9]", "", text.lower()).replace("0", "o").replace("1", "l")
    # OCR can mistake the lower-case L in Hold for an upper-case I.
    text = text.replace("hoidto", "holdto")
    if "holdtoreveal" in text:
        return "Hold to Reveal"
    if "hatchedsomethingspecial" in text:
        return "Special hatch popup"
    return ""


def is_reveal_text(text):
    return bool(reveal_reason(text))


class AlertGuard:
    def __init__(self, cooldown=60.0):
        self.cooldown = cooldown
        self.last_alert = float('-inf')
        self.armed = True
        self.hits = 0
        self.clear_since = None

    def observe(self, hit, now, rearm=12.0):
        if hit is None:  # Failed captures / OCR are not evidence of a clear screen.
            self.hits = 0
            self.clear_since = None
            return False
        if not hit:
            self.hits = 0
            if self.clear_since is None:
                self.clear_since = now
            if now - self.clear_since >= max(12.0, rearm):
                self.armed = True
            return False
        self.clear_since = None
        self.hits += 1
        if self.armed and self.hits >= 2 and now - self.last_alert >= self.cooldown:
            self.armed = False
            self.last_alert = now
            return True
        return False
