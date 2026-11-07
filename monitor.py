from collections import deque
from datetime import datetime

LIMITS = {"ph": (6.0, 9.0), "turb": (None, 50.0), "temp": (None, 40.0), "tds": (None, 1500.0)}
NAMES = {"ph": "pH", "turb": "Turbidity", "temp": "Temperature", "tds": "TDS"}
DEC = {"ph": 1, "turb": 0, "temp": 1, "tds": 0}
KEYS = list(LIMITS)


def violates(k, v):
    lo, hi = LIMITS[k]
    return (lo is not None and v < lo) or v > hi


def classify(v, r):
    o = []
    if "ph" in v:
        o.append("acid spill or dosing error" if r["ph"] < 6 else "alkali spill or dosing error")
    if "turb" in v and "tds" in v:
        o.append("untreated process discharge or treatment bypass")
    elif "turb" in v:
        o.append("solids-rich discharge")
    elif "tds" in v:
        o.append("high dissolved solids")
    if "temp" in v:
        o.append("hot cooling or boiler discharge")
    return "; ".join(o)


class DeviceMonitor:
    def __init__(self, device):
        self.device = device
        self.hist = deque(maxlen=60)
        self.bad = 0
        self.good = 0
        self.alarm = False
        self.was_stuck = False
        self.status = "NORMAL"

    def load(self, readings):
        """Restore history after a restart (no alerts are replayed)."""
        for r in readings:
            self.hist.append(r)

    def update(self, r):
        """Add one reading. Returns a list of (kind, text) events.
        kind: a = alarm, o = back to normal, f = sensor fault"""
        events = []
        now = datetime.now().strftime("%H:%M:%S")
        self.hist.append(r)
        n = len(self.hist)
        v = [k for k in KEYS if violates(k, r[k])]

        rapid = n >= 3 and abs(r["ph"] - self.hist[n - 3]["ph"]) > 1
        last8 = list(self.hist)[-8:]
        stuck = n >= 8 and all(all(h[k] == r[k] for k in KEYS) for h in last8)

        if not stuck:
            if v:
                self.bad += 1
                self.good = 0
            else:
                self.good += 1
                self.bad = 0

        if self.bad >= 3 and not self.alarm:
            self.alarm = True
            vals = ", ".join(f"{NAMES[k]} = {r[k]:.{DEC[k]}f}" for k in v)
            events.append(("a", f"ALERT {self.device}: {vals} at {now}. Possible {classify(v, r)}."))
        if self.alarm and self.good >= 3:
            self.alarm = False
            events.append(("o", f"{self.device}: readings back within limits at {now}."))
        if stuck and not self.was_stuck:
            events.append(("f", f"FAULT {self.device}: readings frozen at {now}. Check the sensors for failure or tampering."))
        self.was_stuck = stuck

        if stuck:
            self.status = "SENSOR FAULT"
        elif self.alarm:
            self.status = "ALARM"
        elif v or rapid:
            self.status = "WARNING"
        else:
            self.status = "NORMAL"
        return events