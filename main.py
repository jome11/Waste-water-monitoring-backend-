import os
import sqlite3
import threading
import time
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from monitor import DeviceMonitor

API_KEY = os.environ.get("WW_API_KEY", "change-me")
OFFLINE_AFTER = 10  # seconds without data = device offline
DB_PATH = os.environ.get("WW_DB", "wastewater.db")

app = FastAPI(title="Wastewater Monitoring API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

lock = threading.Lock()
db = sqlite3.connect(DB_PATH, check_same_thread=False)
db.row_factory = sqlite3.Row
db.executescript("""
CREATE TABLE IF NOT EXISTS readings(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, device TEXT,
  ph REAL, turb REAL, temp REAL, tds REAL);
CREATE INDEX IF NOT EXISTS idx_readings ON readings(device, ts);
CREATE TABLE IF NOT EXISTS alerts(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, device TEXT, kind TEXT, text TEXT);
""")
db.commit()

monitors = {}


def get_mon(device):
    if device not in monitors:
        monitors[device] = DeviceMonitor(device)
    return monitors[device]


def row_to_dict(r):
    return {k: r[k] for k in ("ts", "device", "ph", "turb", "temp", "tds")}


# restore recent history after a restart
for d in db.execute("SELECT DISTINCT device FROM readings").fetchall():
    rows = db.execute(
        "SELECT * FROM readings WHERE device=? ORDER BY ts DESC LIMIT 60", (d["device"],)
    ).fetchall()
    get_mon(d["device"]).load([row_to_dict(r) for r in reversed(rows)])


def check_key(x_api_key: Optional[str] = Header(default=None)):
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="bad or missing X-API-Key")


class Reading(BaseModel):
    device: str = Field(default="WW01", max_length=32)
    ph: float = Field(ge=0, le=14)
    turb: float = Field(ge=0, le=5000)
    temp: float = Field(ge=-10, le=120)
    tds: float = Field(ge=0, le=10000)


@app.post("/api/readings", dependencies=[Depends(check_key)])
def add_reading(r: Reading):
    d = r.model_dump()
    d["ts"] = time.time()
    with lock:
        db.execute(
            "INSERT INTO readings(ts,device,ph,turb,temp,tds) VALUES(?,?,?,?,?,?)",
            (d["ts"], d["device"], d["ph"], d["turb"], d["temp"], d["tds"]),
        )
        events = get_mon(d["device"]).update(d)
        for kind, text in events:
            db.execute(
                "INSERT INTO alerts(ts,device,kind,text) VALUES(?,?,?,?)",
                (d["ts"], d["device"], kind, text),
            )
        db.commit()
        status = get_mon(d["device"]).status
    return {"ok": True, "status": status}


@app.get("/api/state")
def state(device: str = "WW01", alerts: int = 30):
    with lock:
        row = db.execute(
            "SELECT * FROM readings WHERE device=? ORDER BY ts DESC LIMIT 1", (device,)
        ).fetchone()
        al = db.execute(
            "SELECT ts,kind,text FROM alerts WHERE device=? ORDER BY ts DESC LIMIT ?",
            (device, alerts),
        ).fetchall()
        mon = get_mon(device)
        alert_list = [dict(a) for a in al]
        if row is None:
            return {"device": device, "online": False, "status": "OFFLINE", "reading": None, "alerts": alert_list}
        reading = row_to_dict(row)
        online = time.time() - reading["ts"] <= OFFLINE_AFTER
        return {
            "device": device,
            "online": online,
            "status": mon.status if online else "OFFLINE",
            "reading": reading,
            "alerts": alert_list,
        }


@app.get("/api/history")
def history(device: str = "WW01", n: int = 60):
    n = max(1, min(n, 2000))
    with lock:
        rows = db.execute(
            "SELECT * FROM readings WHERE device=? ORDER BY ts DESC LIMIT ?", (device, n)
        ).fetchall()
    return [row_to_dict(r) for r in reversed(rows)]


@app.get("/api/health")
def health():
    return {"ok": True}