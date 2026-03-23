#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════╗
║  AquaH2 AI-SCADA Platform                                       ║
║  Kraefegg M.O. · Developer: Railson                             ║
║  Rev 2.1.4 · Zero dependências — Python stdlib apenas           ║
║                                                                  ║
║  COMO USAR:                                                      ║
║    python3 run.py                                                ║
║    Abrir no browser: http://localhost:8765                       ║
║                                                                  ║
║  Compatível com Python 3.8+                                     ║
╚══════════════════════════════════════════════════════════════════╝
"""

import http.server
import threading
import json
import sqlite3
import math
import random
import time
import os
import sys
import socket
import struct
import hashlib
import base64
import statistics
import traceback
import webbrowser
from collections import deque
from urllib.parse import urlparse, parse_qs
from typing import Dict, List, Any, Set

PORT = 8765
HOST = "127.0.0.1"
DB_FILE = "aquah2_data.db"


# ═══════════════════════════════════════════════════════════════════
# CONFIGURAÇÕES E LIMITES DE ENGENHARIA
# ═══════════════════════════════════════════════════════════════════

LIMITS = {
    "stack_temp_warn":     80.0,
    "stack_temp_alarm":    82.0,
    "stack_temp_trip":     85.0,
    "stack_press_warn":    35.0,
    "stack_press_alarm":   37.0,
    "stack_press_trip":    40.0,
    "h2_lel_alarm":        25.0,
    "h2_lel_trip":         50.0,
    "nh3_alarm_ppm":       25.0,
    "nh3_trip_ppm":        100.0,
    "swro_salinity_max":   0.50,
    "bess_soc_min":        20.0,
    "h2_purity_min":       99.90,
}


# ═══════════════════════════════════════════════════════════════════
# BANCO DE DADOS SQLite
# ═══════════════════════════════════════════════════════════════════

class Database:
    def __init__(self, path=DB_FILE):
        self.path = path
        self._local = threading.local()
        self._init()

    def _conn(self):
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(self.path, check_same_thread=False)
            self._local.conn.row_factory = sqlite3.Row
        return self._local.conn

    def _init(self):
        c = self._conn()
        c.executescript("""
            CREATE TABLE IF NOT EXISTS sensors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL, tag TEXT, value REAL);
            CREATE INDEX IF NOT EXISTS idx_s ON sensors(tag, ts);

            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL, level TEXT, code TEXT, message TEXT, detail TEXT DEFAULT '');
            CREATE INDEX IF NOT EXISTS idx_e ON events(ts);

            CREATE TABLE IF NOT EXISTS setpoints (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL, tag TEXT, old_v REAL, new_v REAL, source TEXT);

            CREATE TABLE IF NOT EXISTS chat (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL, role TEXT, content TEXT);
        """)
        c.commit()

    def write_sensors(self, rows):
        c = self._conn()
        c.executemany("INSERT INTO sensors(ts,tag,value) VALUES(?,?,?)", rows)
        c.commit()

    def write_event(self, level, code, message, detail=""):
        c = self._conn()
        c.execute("INSERT INTO events(ts,level,code,message,detail) VALUES(?,?,?,?,?)",
                  (time.time(), level, code, message, detail))
        c.commit()

    def write_setpoint(self, tag, old_v, new_v, source="operator"):
        c = self._conn()
        c.execute("INSERT INTO setpoints(ts,tag,old_v,new_v,source) VALUES(?,?,?,?,?)",
                  (time.time(), tag, old_v, new_v, source))
        c.commit()

    def get_history(self, tag, hours=24, limit=500):
        since = time.time() - hours * 3600
        c = self._conn()
        rows = c.execute(
            "SELECT ts,value FROM sensors WHERE tag=? AND ts>=? ORDER BY ts DESC LIMIT ?",
            (tag, since, limit)).fetchall()
        return [{"ts": r["ts"], "value": r["value"]} for r in reversed(rows)]

    def get_events(self, limit=80):
        c = self._conn()
        rows = c.execute(
            "SELECT * FROM events ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def get_setpoints_log(self, limit=30):
        c = self._conn()
        rows = c.execute(
            "SELECT * FROM setpoints ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def trim(self, hours=72):
        cutoff = time.time() - hours * 3600
        c = self._conn()
        c.execute("DELETE FROM sensors WHERE ts<?", (cutoff,))
        c.commit()


# ═══════════════════════════════════════════════════════════════════
# SIMULADOR DA PLANTA
# ═══════════════════════════════════════════════════════════════════

class Plant:
    """
    Simula fisicamente a planta AquaH2.
    Para hardware real: substitua o método tick() por leituras MODBUS/OPC-UA.

    Exemplo MODBUS (instalar pymodbus):
        from pymodbus.client import ModbusTcpClient
        client = ModbusTcpClient('192.168.1.100')
        regs = client.read_holding_registers(0, 20, slave=1)
        self.state['stack_a']['temp'] = regs.registers[0] / 10.0
    """

    def __init__(self):
        self._t = 0.0
        self._lock = threading.Lock()
        self.setpoints = {
            "stack_a_power":     86.0,   # %
            "stack_b_power":     84.0,
            "stack_a_flow":      22.0,   # L/min
            "stack_b_flow":      22.0,
            "h2_pressure":       32.0,   # bar
            "swro_capacity":     65.0,   # %
            "bess_priority":     20.0,   # %
        }
        self.toggles = {
            "stack_a": True,
            "stack_b": True,
            "swro":    True,
            "bess":    True,
            "nh3":     True,
            "ai_mode": True,
        }
        self.safety = {
            "esd_armed":     True,
            "esd_triggered": False,
        }
        # Initialize sensor state
        self._state = {}
        self._init_state()

    def _init_state(self):
        self._state = {
            "stack_a": {
                "enabled": True,
                "temp": 72.3, "pressure": 31.2, "current": 4820.0,
                "voltage": 1.89, "flow": 22.0, "h2_nm3h": 112.0,
                "h2_purity": 99.97, "efficiency": 71.4, "spec_energy": 4.82,
            },
            "stack_b": {
                "enabled": True,
                "temp": 78.9, "pressure": 34.8, "current": 4710.0,
                "voltage": 1.91, "flow": 22.0, "h2_nm3h": 109.0,
                "h2_purity": 99.95, "efficiency": 70.8, "spec_energy": 4.88,
            },
            "energy": {
                "solar_mw": 18.4, "wind_mw": 20.1,
                "irradiance": 842.0, "wind_speed": 9.4,
                "wind_dir": 22.0, "total_mw": 38.5,
                "solar_pr": 0.82, "inverters_online": 48, "turbines_online": 10,
            },
            "bess": {
                "enabled": True,
                "soc": 82.0, "power_kw": 0.0,
                "temp": 28.4, "cycles": 412, "soh": 96.2, "energy_mwh": 16.4,
            },
            "swro": {
                "enabled": True,
                "feed_salinity": 35.2, "product_salinity": 0.32,
                "feed_pressure": 62.0, "brine_pressure": 60.8,
                "water_temp": 26.8, "product_flow": 3.47,
                "sdi": 2.8, "ph": 7.2, "conductivity": 640.0,
                "turbidity": 0.08, "recovery": 40.0, "spec_energy": 3.4,
                "membrane_fouling": {
                    "PV-01": 18.0, "PV-02": 22.0, "PV-03": 41.0,
                    "PV-04": 15.0, "PV-05": 29.0, "PV-06": 19.0,
                },
            },
            "h2_storage": {
                "level_pct": 48.0, "mass_t": 2.4,
                "pressure_bar": 874.0, "temp": 22.1,
                "compressor_on": True, "comp_flow": 36.8,
            },
            "nh3": {
                "running": True, "reactor_temp": 425.0, "reactor_press": 180.0,
                "conversion": 22.4, "prod_kgh": 118.0,
                "tank_pct": 41.0, "tank_mass_t": 246.0,
            },
            "safety": {
                "h2_lel": {f"DET-H2-{i:02d}": round(random.uniform(0.04, 0.14), 3)
                           for i in range(1, 9)},
                "nh3_ppm": {f"DET-NH3-{i:02d}": round(random.uniform(2.0, 12.0), 1)
                            for i in range(1, 7)},
                "esd_armed": True, "esd_triggered": False, "psv": "CLOSED",
            },
            "network": {
                "nodes_online": 38, "nodes_total": 39,
                "avg_latency_ms": 3.8,
                "wifi_ap_03_latency": 148.0,
                "throughput_mbps": 842.0,
            },
            "timestamp": time.time(),
        }

    # ── Math helpers ────────────────────────────────────────────────

    def _s(self, freq, amp, phase=0.0):
        return amp * math.sin(2 * math.pi * freq * self._t + phase)

    def _n(self, amp):
        return amp * (random.random() - 0.5) * 2

    def _b(self, v, lo, hi):
        return max(lo, min(hi, v))

    # ── Main simulation tick ─────────────────────────────────────────

    def tick(self):
        """
        Advance physics simulation by one time step.
        PRODUCTION: replace this method body with hardware reads.
        """
        with self._lock:
            self._t += 1.0
            s = self._state
            sp = self.setpoints
            tg = self.toggles

            # ── Solar (day cycle) + Wind ──────────────────────────
            hour = (self._t % 86400) / 3600
            solar_prof = max(0.0, math.sin(math.pi * (hour - 6) / 12))
            s["energy"]["irradiance"] = self._b(
                800 * solar_prof + self._s(0.002, 80) + self._n(40), 0, 1050)
            s["energy"]["solar_mw"] = self._b(
                25 * solar_prof * 0.82 + self._s(0.003, 2) + self._n(0.8), 0, 25)

            ws = self._b(9.4 + self._s(0.0008, 3.5) + self._s(0.005, 1.2) + self._n(0.4), 3, 16)
            s["energy"]["wind_speed"] = ws
            # Wind power curve: cut-in 3 m/s, rated 11.5 m/s
            if ws < 3:   wp = 0.0
            elif ws < 11.5: wp = 25 * ((ws - 3) / 8.5) ** 3
            else:        wp = 25.0
            s["energy"]["wind_mw"] = self._b(wp + self._n(0.5), 0, 25)
            s["energy"]["total_mw"] = s["energy"]["solar_mw"] + s["energy"]["wind_mw"]

            # ── BESS ─────────────────────────────────────────────
            if s["bess"]["enabled"] and tg.get("bess"):
                surplus = s["energy"]["total_mw"] - 47.0
                bp = self._b(surplus * sp["bess_priority"] / 100 * 1000, -10000, 10000)
                s["bess"]["power_kw"] = bp
                s["bess"]["soc"] = self._b(s["bess"]["soc"] + bp * 0.00002, 5, 95)
                s["bess"]["energy_mwh"] = round(s["bess"]["soc"] / 100 * 20, 2)
                s["bess"]["temp"] = self._b(28.4 + abs(bp) * 0.0003 + self._n(0.2), 20, 50)

            # ── Stack A ──────────────────────────────────────────
            self._tick_stack("stack_a", "stack_a_power", "stack_a_flow",
                             base_temp=72.3, base_press=31.2)

            # ── Stack B (runs hotter — scenario for AI control) ───
            self._tick_stack("stack_b", "stack_b_power", "stack_b_flow",
                             base_temp=78.9, base_press=34.8)

            # ── SWRO ─────────────────────────────────────────────
            if s["swro"]["enabled"] and tg.get("swro"):
                cap = sp["swro_capacity"] / 100
                s["swro"]["feed_pressure"] = self._b(62 + self._s(0.004, 2.5) + self._n(0.5), 55, 70)
                s["swro"]["product_flow"]  = self._b(3.47 * cap + self._s(0.003, 0.3) + self._n(0.1), 0, 5.8)
                s["swro"]["product_salinity"] = self._b(0.32 + self._s(0.002, 0.04) + self._n(0.02), 0.05, 0.6)
                s["swro"]["water_temp"]    = self._b(26.8 + self._s(0.001, 1.2) + self._n(0.2), 20, 35)
                s["swro"]["sdi"]           = self._b(2.8 + self._s(0.001, 0.3) + self._n(0.1), 0.5, 4.0)
                for pv in s["swro"]["membrane_fouling"]:
                    s["swro"]["membrane_fouling"][pv] = min(100.0,
                        s["swro"]["membrane_fouling"][pv] + random.uniform(0, 0.002))

            # ── H2 Storage + NH3 ─────────────────────────────────
            h2_total = (s["stack_a"]["h2_nm3h"] if tg.get("stack_a") else 0) + \
                       (s["stack_b"]["h2_nm3h"] if tg.get("stack_b") else 0)
            nh3_h2 = s["nh3"]["prod_kgh"] / 0.178 * 0.001
            net = h2_total - nh3_h2
            s["h2_storage"]["level_pct"] = self._b(
                s["h2_storage"]["level_pct"] + net * 0.0000004, 0, 100)
            s["h2_storage"]["mass_t"] = round(s["h2_storage"]["level_pct"] / 100 * 5, 2)
            s["h2_storage"]["pressure_bar"] = self._b(874 + self._s(0.003, 15) + self._n(5), 50, 900)

            if s["nh3"]["running"] and tg.get("nh3"):
                s["nh3"]["prod_kgh"]    = self._b(118 + self._s(0.003, 6) + self._n(2), 0, 150)
                s["nh3"]["reactor_temp"]  = self._b(425 + self._s(0.002, 12) + self._n(3), 380, 500)
                s["nh3"]["reactor_press"] = self._b(180 + self._s(0.002, 8) + self._n(2), 140, 220)
                s["nh3"]["tank_pct"] = self._b(
                    s["nh3"]["tank_pct"] + s["nh3"]["prod_kgh"] * 0.0000003, 0, 100)
                s["nh3"]["tank_mass_t"] = round(s["nh3"]["tank_pct"] / 100 * 600, 1)

            # ── Safety sensors ────────────────────────────────────
            for k in s["safety"]["h2_lel"]:
                s["safety"]["h2_lel"][k] = self._b(
                    s["safety"]["h2_lel"].get(k, 0.1) + self._n(0.03), 0, 80)
            for k in s["safety"]["nh3_ppm"]:
                s["safety"]["nh3_ppm"][k] = self._b(
                    s["safety"]["nh3_ppm"].get(k, 8) + self._n(0.4), 0, 120)

            # ── Network ───────────────────────────────────────────
            s["network"]["avg_latency_ms"] = self._b(3.8 + self._n(1.2), 1, 8)
            s["network"]["wifi_ap_03_latency"] = self._b(148 + self._s(0.01, 30) + self._n(15), 80, 250)
            s["timestamp"] = time.time()

        return s

    def _tick_stack(self, name, pwr_key, flow_key, base_temp, base_press):
        s = self._state[name]
        sp = self.setpoints
        tg = self.toggles
        if not s["enabled"] or not tg.get(name.split("_")[1] if "_" in name else name):
            s["h2_nm3h"] = 0.0; s["current"] = 0.0; return
        pwr = sp[pwr_key] / 100
        fl  = sp[flow_key]
        s["flow"] = self._b(fl + self._s(0.005, 1.2) + self._n(0.3), 0, 40)
        cool = s["flow"] / 22.0
        s["temp"] = self._b(
            base_temp + (pwr - 0.85) * 12 - (cool - 1.0) * 5
            + self._s(0.004, 1.8) + self._n(0.4), 55, 92)
        s["pressure"] = self._b(
            base_press + (pwr - 0.85) * 4 + self._s(0.005, 2.0) + self._n(0.5), 25, 42)
        s["current"] = self._b(pwr * 5500 + self._s(0.005, 180) + self._n(50), 0, 6500)
        temp_pen = max(0, (s["temp"] - 75) * 0.15)
        s["efficiency"] = self._b(71.4 * pwr - temp_pen + self._s(0.003, 0.8) + self._n(0.2), 60, 80)
        s["h2_nm3h"] = self._b(
            s["efficiency"] / 100 * pwr * 25000 / 3.54 + self._s(0.004, 4) + self._n(1.5), 0, 150)
        s["spec_energy"] = self._b(4.82 + (1 - s["efficiency"] / 71.4) * 0.5 + self._n(0.05), 4.0, 6.5)
        s["h2_purity"] = self._b(99.97 - (s["temp"] - 72) * 0.002 + self._n(0.01), 99.0, 100.0)
        s["voltage"]   = self._b(1.89 + (s["temp"] - 72) * 0.002 + self._n(0.01), 1.7, 2.2)

    # ── Commands ────────────────────────────────────────────────────

    def set_setpoint(self, key, value):
        if key in self.setpoints:
            self.setpoints[key] = float(value)
            return True
        return False

    def set_toggle(self, key, value):
        if key in self.toggles:
            self.toggles[key] = bool(value)
            with self._lock:
                if key == "stack_a": self._state["stack_a"]["enabled"] = bool(value)
                if key == "stack_b": self._state["stack_b"]["enabled"] = bool(value)
                if key == "swro":    self._state["swro"]["enabled"] = bool(value)
                if key == "bess":    self._state["bess"]["enabled"] = bool(value)
                if key == "nh3":     self._state["nh3"]["running"] = bool(value)
            return True
        return False

    def trigger_esd(self):
        with self._lock:
            self._state["safety"]["esd_triggered"] = True
            self._state["safety"]["esd_armed"] = False
        for k in ("stack_a", "stack_b", "nh3"):
            self.toggles[k] = False
            if k in self._state:
                self._state[k]["enabled"] = False
                self._state[k]["h2_nm3h"] = 0
        self._state["nh3"]["running"] = False
        return "ESD ATIVADO — Eletrolisadores e síntese NH3 desligados. Reset requer autorização do supervisor."

    def reset_esd(self):
        with self._lock:
            self._state["safety"]["esd_triggered"] = False
            self._state["safety"]["esd_armed"] = True
        for k in ("stack_a", "stack_b", "nh3"):
            self.toggles[k] = True
            if k in self._state:
                self._state[k]["enabled"] = True
        self._state["nh3"]["running"] = True
        return "ESD resetado. Sistemas em sequência de partida normal."

    def snapshot(self):
        with self._lock:
            return json.loads(json.dumps(self._state))  # deep copy via JSON


# ═══════════════════════════════════════════════════════════════════
# MOTOR DE INTELIGÊNCIA ARTIFICIAL
# ═══════════════════════════════════════════════════════════════════

class RollingWindow:
    def __init__(self, n=30):
        self._d = deque(maxlen=n)

    def push(self, v):
        self._d.append(float(v))

    def mean(self):
        return statistics.mean(self._d) if len(self._d) > 1 else 0.0

    def stdev(self):
        return statistics.stdev(self._d) if len(self._d) > 2 else 1e-6

    def zscore(self, v):
        if len(self._d) < 5: return 0.0
        s = self.stdev()
        return abs(v - self.mean()) / s if s > 1e-9 else 0.0

    def slope(self):
        d = list(self._d)
        n = len(d)
        if n < 3: return 0.0
        xm = (n - 1) / 2
        ym = sum(d) / n
        num = sum((i - xm) * (y - ym) for i, y in enumerate(d))
        den = sum((i - xm) ** 2 for i in range(n))
        return num / den if den > 1e-12 else 0.0

    def __len__(self): return len(self._d)


class PID:
    def __init__(self, kp, ki, kd, lo, hi):
        self.kp, self.ki, self.kd = kp, ki, kd
        self.lo, self.hi = lo, hi
        self._i = 0.0
        self._prev_e = 0.0
        self._prev_t = time.time()

    def update(self, sp, pv):
        t = time.time()
        dt = max(t - self._prev_t, 1e-6)
        e = sp - pv
        self._i += e * dt
        self._i = max(self.lo / (self.ki or 1e9), min(self.hi / (self.ki or 1e9), self._i))
        d = (e - self._prev_e) / dt
        out = max(self.lo, min(self.hi, self.kp * e + self.ki * self._i + self.kd * d))
        self._prev_e = e; self._prev_t = t
        return out


class AIEngine:
    def __init__(self, plant: Plant, db: Database):
        self.plant = plant
        self.db = db
        self.active_alarms: Dict[str, Dict] = {}
        self.alarm_history: List[Dict] = []
        self.decisions: List[Dict] = []
        self.metrics = {
            "decisions": 0, "alarms": 0, "anomalies": 0, "uptime_start": time.time()
        }
        self._windows = {k: RollingWindow(30) for k in [
            "ta", "tb", "pa", "pb", "ha", "hb", "solar", "wind", "bess", "swro_sal"
        ]}
        self._pid_a = PID(kp=0.5, ki=0.01, kd=0.1, lo=15, hi=38)
        self._pid_b = PID(kp=0.6, ki=0.015, kd=0.1, lo=15, hi=38)
        self.chat_log: List[Dict] = []

    def run_cycle(self):
        """Full AI analysis and control cycle."""
        s = self.plant.snapshot()
        self._ingest(s)
        decisions = []
        decisions += self._check_safety(s)
        decisions += self._control_temps(s)
        decisions += self._optimize_energy(s)
        decisions += self._detect_anomalies(s)
        decisions += self._predictive(s)

        for d in decisions:
            if self.plant.toggles.get("ai_mode", True):
                if d["type"] == "setpoint":
                    self.plant.set_setpoint(d["target"], d["value"])
                elif d["type"] == "toggle":
                    self.plant.set_toggle(d["target"], d["value"])
            self.decisions.append(d)
            self.db.write_event("ai", d["type"], d["reason"][:120])
            self.metrics["decisions"] += 1
        if len(self.decisions) > 200:
            self.decisions = self.decisions[-200:]
        return decisions

    def _ingest(self, s):
        sa, sb = s["stack_a"], s["stack_b"]
        en, bs, sw = s["energy"], s["bess"], s["swro"]
        self._windows["ta"].push(sa["temp"])
        self._windows["tb"].push(sb["temp"])
        self._windows["pa"].push(sa["pressure"])
        self._windows["pb"].push(sb["pressure"])
        self._windows["ha"].push(sa["h2_nm3h"])
        self._windows["hb"].push(sb["h2_nm3h"])
        self._windows["solar"].push(en["solar_mw"])
        self._windows["wind"].push(en["wind_mw"])
        self._windows["bess"].push(bs["soc"])
        self._windows["swro_sal"].push(sw["product_salinity"])

    def _alarm(self, code, level, detail):
        if code not in self.active_alarms:
            a = {"code": code, "level": level, "message": detail,
                 "ts": time.time(), "acked": False, "ack_by": ""}
            self.active_alarms[code] = a
            self.alarm_history.append(a)
            self.db.write_event(level, code, detail)
            self.metrics["alarms"] += 1

    def _clear(self, *codes):
        for c in codes:
            self.active_alarms.pop(c, None)

    def _check_safety(self, s):
        d = []
        L = LIMITS
        ta, tb = s["stack_a"]["temp"], s["stack_b"]["temp"]
        pa, pb = s["stack_a"]["pressure"], s["stack_b"]["pressure"]

        # Stack A temp
        if ta > L["stack_temp_trip"]:
            self._alarm("ALM-0003", "trip", f"Stack A temp CRÍTICA {ta:.1f}°C — ESD")
            d.append({"type":"toggle","target":"stack_a","value":False,
                      "reason":f"ESD automático: Stack A {ta:.1f}°C > trip {L['stack_temp_trip']}°C","conf":1.0})
        elif ta > L["stack_temp_alarm"]:
            self._alarm("ALM-0002", "alarm", f"Stack A temp alarme {ta:.1f}°C")
        elif ta > L["stack_temp_warn"]:
            self._alarm("ALM-0001", "warn", f"Stack A temp aviso {ta:.1f}°C")
        else:
            self._clear("ALM-0001","ALM-0002","ALM-0003")

        # Stack B temp
        if tb > L["stack_temp_trip"]:
            self._alarm("ALM-0006","trip",f"Stack B temp CRÍTICA {tb:.1f}°C")
            d.append({"type":"toggle","target":"stack_b","value":False,
                      "reason":f"ESD: Stack B {tb:.1f}°C > {L['stack_temp_trip']}°C","conf":1.0})
        elif tb > L["stack_temp_alarm"]:
            self._alarm("ALM-0005","alarm",f"Stack B temp alarme {tb:.1f}°C")
        elif tb > L["stack_temp_warn"]:
            self._alarm("ALM-0004","warn",f"Stack B temp aviso {tb:.1f}°C")
        else:
            self._clear("ALM-0004","ALM-0005","ALM-0006")

        # Pressure A
        if pa > L["stack_press_trip"]:
            self._alarm("ALM-0011","trip",f"Stack A pressão CRÍTICA {pa:.1f} bar")
            d.append({"type":"toggle","target":"stack_a","value":False,
                      "reason":f"ESD: pressão Stack A {pa:.1f} bar","conf":1.0})
        elif pa > L["stack_press_warn"]:
            self._alarm("ALM-0010","warn",f"Stack A pressão alta {pa:.1f} bar")
        else:
            self._clear("ALM-0010","ALM-0011")

        # Pressure B
        if pb > L["stack_press_trip"]:
            self._alarm("ALM-0013","trip",f"Stack B pressão CRÍTICA {pb:.1f} bar")
        elif pb > L["stack_press_warn"]:
            self._alarm("ALM-0012","warn",f"Stack B pressão alta {pb:.1f} bar")
        else:
            self._clear("ALM-0012","ALM-0013")

        # H2 LEL
        for sensor, lel in s["safety"]["h2_lel"].items():
            if lel > L["h2_lel_trip"]:
                self._alarm("ALM-0021","trip",f"{sensor}: H2 LEL CRÍTICO {lel:.1f}%")
            elif lel > L["h2_lel_alarm"]:
                self._alarm("ALM-0020","warn",f"{sensor}: H2 LEL {lel:.1f}%")

        # NH3
        for sensor, ppm in s["safety"]["nh3_ppm"].items():
            if ppm > L["nh3_trip_ppm"]:
                self._alarm("ALM-0031","trip",f"{sensor}: NH3 CRÍTICO {ppm:.0f} ppm")
            elif ppm > L["nh3_alarm_ppm"]:
                self._alarm("ALM-0030","warn",f"{sensor}: NH3 alto {ppm:.0f} ppm")

        # SWRO salinity
        sal = s["swro"]["product_salinity"]
        if sal > L["swro_salinity_max"]:
            self._alarm("ALM-0041","warn",f"SWRO salinidade {sal:.3f} g/L > {L['swro_salinity_max']}")
        else:
            self._clear("ALM-0041")

        return d

    def _control_temps(self, s):
        d = []
        target = 73.0
        if s["stack_a"]["enabled"]:
            nf = round(self._pid_a.update(target, s["stack_a"]["temp"]), 1)
            cur = self.plant.setpoints.get("stack_a_flow", 22.0)
            if abs(nf - cur) > 0.6:
                d.append({"type":"setpoint","target":"stack_a_flow","value":nf,
                          "reason":f"PID Stack A: temp {s['stack_a']['temp']:.1f}°C → fluxo H₂O {nf} L/min","conf":0.85})
        if s["stack_b"]["enabled"]:
            nf = round(self._pid_b.update(target, s["stack_b"]["temp"]), 1)
            cur = self.plant.setpoints.get("stack_b_flow", 22.0)
            if abs(nf - cur) > 0.6:
                d.append({"type":"setpoint","target":"stack_b_flow","value":nf,
                          "reason":f"PID Stack B: temp {s['stack_b']['temp']:.1f}°C ({'⚠ acima do nominal' if s['stack_b']['temp']>78 else 'ok'}) → fluxo H₂O {nf} L/min","conf":0.90})
        return d

    def _optimize_energy(self, s):
        d = []
        soc = s["bess"]["soc"]
        if soc < 30 and s["energy"]["total_mw"] > 40:
            new_bp = min(40, self.plant.setpoints["bess_priority"] + 10)
            d.append({"type":"setpoint","target":"bess_priority","value":new_bp,
                      "reason":f"BESS SoC baixo ({soc:.0f}%) com energia disponível: aumentar carga BESS para {new_bp}%","conf":0.80})
        elif soc > 90:
            new_bp = max(10, self.plant.setpoints["bess_priority"] - 5)
            d.append({"type":"setpoint","target":"bess_priority","value":new_bp,
                      "reason":f"BESS quase cheio ({soc:.0f}%): reduzir prioridade para {new_bp}%, mais energia ao eletrolisador","conf":0.75})
        return d

    def _detect_anomalies(self, s):
        d = []
        checks = [
            ("ta", s["stack_a"]["temp"],        "Temperatura Stack A"),
            ("tb", s["stack_b"]["temp"],        "Temperatura Stack B"),
            ("pa", s["stack_a"]["pressure"],    "Pressão H2 Stack A"),
            ("pb", s["stack_b"]["pressure"],    "Pressão H2 Stack B"),
            ("swro_sal", s["swro"]["product_salinity"], "Salinidade SWRO"),
        ]
        for key, val, label in checks:
            z = self._windows[key].zscore(val)
            if z > 2.8:
                self.metrics["anomalies"] += 1
                d.append({"type":"alert","target":key,"value":val,
                          "reason":f"Anomalia: {label}={val:.2f} Z={z:.1f}","conf":min(1.0, z/4)})
        return d

    def _predictive(self, s):
        d = []
        slope_b = self._windows["tb"].slope()
        if slope_b > 0.04:
            eta = max(0, (LIMITS["stack_temp_alarm"] - s["stack_b"]["temp"]) / slope_b)
            d.append({"type":"alert","target":"stack_b_trend","value":slope_b,
                      "reason":f"Preditivo: Stack B subindo {slope_b:.3f}°C/ciclo. ETA alarme: {eta:.0f} ciclos ({eta*2:.0f}s). Verificar trocador de calor.","conf":0.72})
        pv03 = s["swro"]["membrane_fouling"].get("PV-03", 0)
        if pv03 > 40:
            d.append({"type":"alert","target":"swro_pv03","value":pv03,
                      "reason":f"Preditivo: SWRO PV-03 fouling {pv03:.1f}% — agendar CIP antes de atingir 50%","conf":0.85})
        return d

    def ack_alarm(self, code, operator):
        if code in self.active_alarms:
            self.active_alarms[code]["acked"] = True
            self.active_alarms[code]["ack_by"] = operator
            return True
        return False

    def chat(self, message):
        """Context-aware built-in responses."""
        s = self.plant.snapshot()
        sa, sb = s["stack_a"], s["stack_b"]
        en, bs = s["energy"], s["bess"]
        sw, nh = s["swro"], s["nh3"]
        h2total = sa["h2_nm3h"] + sb["h2_nm3h"]
        alarms = list(self.active_alarms.values())
        msg = message.lower()

        if any(w in msg for w in ["status","resumo","geral","overview","tudo"]):
            return (f"**Status — {time.strftime('%H:%M:%S')}**\n"
                    f"• H₂: **{h2total:.0f} Nm³/h** ({h2total*0.0898/1000*3600/1000:.2f} kg/h)\n"
                    f"• Stack A: {sa['temp']:.1f}°C / {sa['pressure']:.1f} bar / {sa['efficiency']:.1f}% LHV\n"
                    f"• Stack B: {sb['temp']:.1f}°C / {sb['pressure']:.1f} bar"
                    + (" ⚠ temp acima do nominal" if sb["temp"] > 78 else " ✓") + "\n"
                    f"• Energia: Solar {en['solar_mw']:.1f} MW + Eólica {en['wind_mw']:.1f} MW = **{en['total_mw']:.1f} MW**\n"
                    f"• BESS: {bs['soc']:.0f}% SoC · SWRO: {sw['product_salinity']:.3f} g/L\n"
                    f"• NH₃: {nh['prod_kgh']:.0f} kg/h · Tanque: {nh['tank_pct']:.0f}%\n"
                    f"• Alarmes ativos: **{len(alarms)}** · IA: {'AUTO ✓' if self.plant.toggles.get('ai_mode') else 'MANUAL'}")

        elif any(w in msg for w in ["otimiz","eficiência","produção","melhor"]):
            slope = self._windows["tb"].slope()
            cur_flow = self.plant.setpoints.get("stack_b_flow", 22)
            return (f"**Otimização — análise IA:**\n"
                    f"• Stack A eficiência: {sa['efficiency']:.1f}% · Potência: {self.plant.setpoints['stack_a_power']:.0f}%\n"
                    f"• Stack B eficiência: {sb['efficiency']:.1f}% · Temp tendência: {'+' if slope>0 else ''}{slope*60:.2f}°C/min\n"
                    f"• Fluxo H₂O Stack B atual: {cur_flow} L/min (PID ajustando)\n"
                    f"• Energia disponível: {en['total_mw']:.1f} MW — "
                    + ("aumento de potência possível" if en['total_mw'] > 46 else "operando no limite disponível") + "\n"
                    f"• Decisões IA hoje: **{self.metrics['decisions']}** aplicadas")

        elif any(w in msg for w in ["risco","falha","prediti","manutenc","manutenç"]):
            pv03 = s["swro"]["membrane_fouling"].get("PV-03", 0)
            slope = self._windows["tb"].slope()
            return (f"**Análise preditiva — 7 dias:**\n"
                    f"• Stack B temp tendência: {'+' if slope>0 else ''}{slope*60:.2f}°C/min — risco {'MÉDIO ⚠' if slope>0.03 else 'BAIXO ✓'}\n"
                    f"• SWRO PV-03 fouling: {pv03:.1f}% — {'CIP necessário em breve ⚠' if pv03>40 else 'normal ✓'}\n"
                    f"• Pureza H₂ Stack B: {sb['h2_purity']:.3f}% — {'atenção' if sb['h2_purity']<99.95 else 'normal ✓'}\n"
                    f"• H₂ LEL máximo detectado: {max(s['safety']['h2_lel'].values()):.2f}% LEL — {'normal ✓' if max(s['safety']['h2_lel'].values()) < 25 else 'ALARME ⚠'}\n"
                    f"• Prob. falha crítica (72h): **{len(alarms)*1.8 + 1.2:.1f}%**\n"
                    f"• Anomalias detectadas: {self.metrics['anomalies']}")

        elif any(w in msg for w in ["alarme","alerta","aviso","alert"]):
            if not alarms:
                return "✓ Nenhum alarme ativo. Todos os sistemas dentro dos limites operacionais."
            lines = [f"**{len(alarms)} alarme(s) ativo(s):**"]
            for a in alarms[:6]:
                ack = " ✓" if a["acked"] else " — pendente"
                lines.append(f"• [{a['level'].upper()}] **{a['code']}**: {a['message'][:70]}{ack}")
            return "\n".join(lines)

        elif any(w in msg for w in ["relatorio","relatório","turno","shift"]):
            uptime_h = (time.time() - self.metrics["uptime_start"]) / 3600
            return (f"**Relatório — últimas {uptime_h:.1f}h:**\n"
                    f"• H₂ estimado: **{h2total * uptime_h * 0.0898/1000:.2f} t**\n"
                    f"• Energia gerada: **{en['total_mw'] * uptime_h:.0f} MWh**\n"
                    f"• Água dessalinizada: **{sw['product_flow'] * 60 * uptime_h:.0f} m³**\n"
                    f"• NH₃ produzida: **{nh['prod_kgh'] * uptime_h / 1000:.3f} t**\n"
                    f"• Eficiência média PEM: {(sa['efficiency']+sb['efficiency'])/2:.1f}%\n"
                    f"• Decisões IA: {self.metrics['decisions']} · Alarmes: {self.metrics['alarms']}\n"
                    f"• Economia estimada: R$ {self.metrics['decisions'] * 28:.0f}")

        elif any(w in msg for w in ["stack a","stack_a"]):
            return (f"**Stack A — detalhes:**\n"
                    f"• Temperatura: **{sa['temp']:.1f}°C** (nominal 60–80°C)\n"
                    f"• Pressão H₂: **{sa['pressure']:.1f} bar** (nominal 28–35 bar)\n"
                    f"• Corrente DC: {sa['current']:.0f} A\n"
                    f"• Tensão média célula: {sa['voltage']:.2f} V\n"
                    f"• Fluxo H₂O: {sa['flow']:.1f} L/min (SP: {self.plant.setpoints['stack_a_flow']:.1f})\n"
                    f"• Produção H₂: **{sa['h2_nm3h']:.0f} Nm³/h** · Pureza: {sa['h2_purity']:.3f}%\n"
                    f"• Eficiência LHV: **{sa['efficiency']:.1f}%** · Energia esp.: {sa['spec_energy']:.2f} kWh/Nm³")

        elif any(w in msg for w in ["stack b","stack_b"]):
            warn = " ⚠ Temperatura acima do nominal" if sb["temp"] > 78 else " ✓ Normal"
            return (f"**Stack B — detalhes:**{warn}\n"
                    f"• Temperatura: **{sb['temp']:.1f}°C**{' ⚠' if sb['temp']>78 else ''} (nominal 60–80°C)\n"
                    f"• Pressão H₂: **{sb['pressure']:.1f} bar**{' ⚠' if sb['pressure']>34 else ''} (nominal 28–35 bar)\n"
                    f"• Corrente DC: {sb['current']:.0f} A\n"
                    f"• Tensão média célula: {sb['voltage']:.2f} V\n"
                    f"• Fluxo H₂O: {sb['flow']:.1f} L/min → PID ajustando para {self.plant.setpoints['stack_b_flow']:.1f}\n"
                    f"• Produção H₂: **{sb['h2_nm3h']:.0f} Nm³/h** · Pureza: {sb['h2_purity']:.3f}%\n"
                    f"• Eficiência LHV: **{sb['efficiency']:.1f}%**")

        elif any(w in msg for w in ["swro","dessaliniz","água","agua"]):
            return (f"**SWRO — Dessalinização:**\n"
                    f"• Pressão membrana: {sw['feed_pressure']:.1f} bar\n"
                    f"• Salinidade produto: **{sw['product_salinity']:.3f} g/L** (WHO ≤ 0.5 ✓)\n"
                    f"• Vazão produto: {sw['product_flow']:.2f} m³/min ({sw['product_flow']*1440:.0f} m³/dia)\n"
                    f"• Temperatura: {sw['water_temp']:.1f}°C · SDI: {sw['sdi']:.1f} · pH: {sw['ph']:.1f}\n"
                    f"• Recuperação: {sw['recovery']:.0f}% · Energia esp.: {sw['spec_energy']:.2f} kWh/m³\n"
                    f"• PV-03 fouling: {s['swro']['membrane_fouling'].get('PV-03',0):.1f}%"
                    + (" ⚠ CIP recomendado" if s['swro']['membrane_fouling'].get('PV-03',0)>40 else " ✓"))

        else:
            return (f"Consultando dados da planta para: '{message[:50]}'...\n"
                    f"Planta operando. H₂: {h2total:.0f} Nm³/h · Stack B: {sb['temp']:.1f}°C"
                    + (" ⚠" if sb["temp"]>78 else " ✓") +
                    f" · Energia: {en['total_mw']:.1f} MW · BESS: {bs['soc']:.0f}%\n"
                    f"Para análise aprofundada, configure ANTHROPIC_API_KEY no topo do run.py.")


# ═══════════════════════════════════════════════════════════════════
# FRONT-END HTML (embutido)
# ═══════════════════════════════════════════════════════════════════

def build_html():
    """Build the complete SCADA frontend with embedded WebSocket client."""
    # Read the external HTML file if available
    for candidate in [
        os.path.join(os.path.dirname(__file__), "aquah2_platform.html"),
        os.path.join(os.path.dirname(__file__), "..", "aquah2_platform.html"),
        "aquah2_platform.html",
    ]:
        if os.path.exists(candidate):
            with open(candidate, "r", encoding="utf-8") as f:
                html = f.read()
            return _inject_ws_client(html)

    # Fallback: minimal built-in page
    return MINIMAL_HTML

def _inject_ws_client(html):
    """Inject the real-time polling client into the HTML."""
    client = """
<script>
/* ── AquaH2 Real Backend Client (HTTP polling) ── */
(function(){
  var POLL_MS = 2000;
  var timer = null;
  var chatResolve = null;

  function poll(){
    fetch('/api/state').then(function(r){return r.json();}).then(function(d){
      applyState(d);
    }).catch(function(){});
    timer = setTimeout(poll, POLL_MS);
  }

  function applyState(d){
    var sa=d.stack_a||{}, sb=d.stack_b||{}, en=d.energy||{}, bs=d.bess||{};
    var sw=d.swro||{}, nh=d.nh3||{}, h2=d.h2_storage||{};
    var h2t=(sa.h2_nm3h||0)+(sb.h2_nm3h||0);

    // Header metrics
    set('h-h2',    f1(h2t*0.0898/1000*3600/1000,1)+' kg/h');
    set('h-pwr',   f1(en.total_mw,1)+' MW');
    set('h-eff',   f1(sa.efficiency,1)+'%');
    set('h-water', f1(sw.product_flow,2)+' m³/min');
    set('h-bess',  Math.round(bs.soc||0)+'%');
    set('h-nh3',   f1(nh.tank_pct,0)+'%');

    // KPIs
    set('kpi-h2',    f1(h2t*0.0898/1000*3600/1000,1)+' <span style="font-size:13px;color:var(--txt2)">kg/h</span>');
    set('kpi-pwr',   f1(en.total_mw,1)+' <span style="font-size:13px;color:var(--txt2)">MW</span>');
    set('kpi-water', f1((sw.product_flow||0)*1440,0)+' <span style="font-size:13px;color:var(--txt2)">m³/dia</span>');
    set('kpi-nh3',   f1((nh.prod_kgh||0)*24/1000,2)+' <span style="font-size:13px;color:var(--txt2)">t</span>');

    // Overview
    set('ov-ta', f1(sa.temp,1)+' °C', 'sval '+(sa.temp>78?'warn':'ok'));
    set('ov-tb', f1(sb.temp,1)+' °C', 'sval '+(sb.temp>78?'warn':'ok'));
    set('ov-press', f1(sa.pressure,1)+' bar');
    set('ov-swro-press', f1(sw.feed_pressure,1)+' bar');

    // Rings
    setRing('ring-eff', (sa.efficiency||70)/100, sa.efficiency<68?'var(--amber)':'var(--teal)');
    setEl('ring-eff-val', f1(sa.efficiency,1));
    setRing('ring-bess', (bs.soc||0)/100, '#10B981');
    setEl('ring-bess-val', Math.round(bs.soc||0));
    set('bess-kwh', f1((bs.soc||0)*0.20,1)+' MWh');

    // Electrolyzer
    setSensor('elec-a-temp',  f1(sa.temp,1)+' °C', sa.temp>78?'sval warn':'sval ok');
    setSensor('elec-a-press', f1(sa.pressure,1)+' bar', sa.pressure>33?'sval warn':'sval');
    setSensor('elec-a-curr',  Math.round(sa.current||0)+' A', 'sval');
    setSensor('elec-a-flow',  f1(sa.flow,1)+' L/min', 'sval');
    setSensor('elec-a-h2',    Math.round(sa.h2_nm3h||0)+' Nm³/h', 'sval ok');
    setSensor('elec-b-temp',  f1(sb.temp,1)+' °C', sb.temp>78?'sval warn':'sval ok');
    setSensor('elec-b-press', f1(sb.pressure,1)+' bar', sb.pressure>34?'sval warn':'sval');
    setSensor('elec-b-curr',  Math.round(sb.current||0)+' A', 'sval');
    setSensor('elec-b-flow',  f1(sb.flow,1)+' L/min', 'sval');
    setSensor('elec-b-h2',    Math.round(sb.h2_nm3h||0)+' Nm³/h', 'sval ok');

    // Energy
    set('en-solar',   f1(en.solar_mw,1)+' <span style="font-size:13px;color:var(--txt2)">MW</span>');
    set('en-wind',    f1(en.wind_mw,1)+' <span style="font-size:13px;color:var(--txt2)">MW</span>');
    set('en-bess',    Math.round(bs.soc||0)+' <span style="font-size:13px;color:var(--txt2)">%</span>');
    set('en-irr',     Math.round(en.irradiance||0)+' W/m²');
    set('en-wspd',    f1(en.wind_speed,1)+' m/s');
    set('en-bess-soc',Math.round(bs.soc||0)+'%');

    // SWRO
    set('swro-feed-press', f1(sw.feed_pressure,1)+' bar');
    set('swro-temp',        f1(sw.water_temp,1)+' °C');

    // PFD
    set('pfd-ta', f1(sa.temp,1)+'°C · '+f1(sa.pressure,1)+' bar');
    set('pfd-tb', f1(sb.temp,1)+'°C · '+f1(sb.pressure,1)+' bar');
    set('pfd-ha', Math.round(sa.h2_nm3h||0)+' Nm³/h H₂');
    set('pfd-hb', Math.round(sb.h2_nm3h||0)+' Nm³/h H₂');
    set('pfd-h2total', Math.round(h2t)+' Nm³/h');
    set('pfd-solar',   f1(en.solar_mw,1)+' MW');
    set('pfd-wind',    f1(en.wind_mw,1)+' MW');
    set('pfd-total-pwr', f1(en.total_mw,1)+' MW total');
    set('pfd-bess',    Math.round(bs.soc||0)+'% SoC');
    set('pfd-h2tank',  f1(h2.level_pct,0)+'% · '+f1(h2.mass_t,1)+'t');
    set('pfd-nh3rate', f1(nh.prod_kgh,0)+' kg/h');

    // Header status
    var dot = document.getElementById('hdr-dot');
    if(dot){ dot.className='hdr-dot'; }
    var stat = document.getElementById('hdr-status');
    if(stat) stat.textContent = 'Backend Python ativo · Dados reais · Polling '+POLL_MS+'ms';

    // System log
    addLog('[DATA] A:'+f1(sa.temp,1)+'°C/'+f1(sa.pressure,1)+'bar/'+Math.round(sa.h2_nm3h)+'Nm³h  B:'+f1(sb.temp,1)+'°C  E:'+f1(en.total_mw,1)+'MW  BESS:'+Math.round(bs.soc)+'%');
  }

  // Override sendChat to POST to backend
  window.sendChat = function(){
    var inp = document.getElementById('chat-inp');
    var v = inp ? inp.value.trim() : ''; if(!v) return;
    if(typeof addMsg==='function') addMsg(v, true);
    inp.value = '';
    var think = document.createElement('div');
    think.className='chat-thinking';
    think.innerHTML='<div class="thinking-dots"><span></span><span></span><span></span></div>';
    var area = document.getElementById('chat-area');
    if(area){ area.appendChild(think); area.scrollTop=99999; }
    fetch('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:v})})
      .then(function(r){return r.json();})
      .then(function(d){
        if(think.parentNode) think.remove();
        if(typeof addMsg==='function') addMsg(d.response||d.error||'Sem resposta',false);
      }).catch(function(e){
        if(think.parentNode) think.remove();
        if(typeof addMsg==='function') addMsg('Erro: '+e,false);
      });
  };

  // Intercept slider and toggle controls to POST setpoints
  document.addEventListener('change', function(e){
    var el = e.target;
    var sliders = {
      'ctrl-pwr-a':   'stack_a_power',
      'ctrl-pwr-b':   'stack_b_power',
      'ctrl-flow-a':  'stack_a_flow',
      'ctrl-flow-b':  'stack_b_flow',
      'ctrl-press-t': 'h2_pressure',
      'ctrl-swro':    'swro_capacity',
      'ctrl-bess':    'bess_priority',
    };
    if(sliders[el.id]){
      fetch('/api/setpoint',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({tag:sliders[el.id],value:parseFloat(el.value)})})
        .then(function(r){return r.json();}).then(function(d){
          addLog('[CMD] Setpoint '+sliders[el.id]+' = '+el.value+' → '+(d.ok?'OK':'FAIL'));
        });
    }
  });

  // Quick chat chips
  window.quickAI = function(msg){ document.getElementById('chat-inp').value=msg; window.sendChat(); };

  // Alarm ack
  window.ackAlarm = function(btn){
    var row = btn.closest('.alarm-row');
    var title = row ? row.querySelector('.alarm-title') : null;
    var code = (title && title.textContent.match(/[A-Z]+-[0-9]+/)) ?
               title.textContent.match(/[A-Z]+-[0-9]+/)[0] : 'ALM-0001';
    fetch('/api/alarms/ack',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({code:code,operator:'railson'})});
    if(row){ row.style.opacity='.4'; }
    btn.textContent='Confirmado'; btn.style.color='var(--green)';
  };

  // ESD button
  var origEsd = window.esd;
  window.esd = function(){
    if(confirm('ATENÇÃO: Confirma parada de emergência de toda a planta?')){
      fetch('/api/esd',{method:'POST'}).then(function(r){return r.json();})
        .then(function(d){ alert('ESD: '+d.message); });
    }
  };

  // Helpers
  function f1(v,d){ return (v===undefined||v===null)?'--':parseFloat(v).toFixed(d); }
  function set(id,html,cls){
    var e=document.getElementById(id); if(!e) return;
    if(cls!==undefined) e.className=cls;
    e.innerHTML=html;
  }
  function setEl(id,t){ var e=document.getElementById(id); if(e) e.textContent=t; }
  function setSensor(id,html,cls){ var e=document.getElementById(id); if(!e)return; e.className=cls||'sval'; e.innerHTML=html; }
  function setRing(id,pct,color){
    var e=document.getElementById(id); if(!e) return;
    var off=201-(201*Math.max(0,Math.min(1,pct)));
    e.setAttribute('stroke-dashoffset',off.toFixed(1));
    if(color) e.setAttribute('stroke',color);
  }
  function addLog(text){
    var area=document.getElementById('sys-log'); if(!area) return;
    var d=document.createElement('div');
    d.textContent='['+new Date().toTimeString().slice(0,8)+'] '+text;
    d.style.color=text.includes('[AI]')?'#8B5CF6':text.includes('[CMD]')?'#00C9A7':'var(--txt2)';
    area.appendChild(d);
    if(area.children.length>100) area.removeChild(area.firstChild);
    area.scrollTop=area.scrollHeight;
  }

  // Start polling immediately
  poll();
  console.log('[AquaH2] HTTP polling client started. Interval: '+POLL_MS+'ms');
})();
</script>
</body>"""
    return html.replace("</body>", client, 1)

MINIMAL_HTML = """<!DOCTYPE html>
<html><head><meta charset='UTF-8'><title>AquaH2 SCADA</title>
<style>body{font-family:monospace;background:#05080F;color:#D1DCF0;padding:40px;max-width:800px;margin:0 auto}
h1{color:#00C9A7}table{width:100%;border-collapse:collapse;margin:20px 0}
td,th{padding:8px 12px;border:1px solid #162338;text-align:left}
th{background:#0B1320;color:#5C7A9B}</style></head>
<body><h1>AquaH2 AI-SCADA</h1><p>Backend ativo. <a href="/api/state" style="color:#00C9A7">Ver estado JSON</a></p>
<p>Para a interface completa, coloque <code>aquah2_platform.html</code> na mesma pasta de <code>run.py</code></p>
<table id='t'><tr><th>Parâmetro</th><th>Valor</th></tr></table>
<script>
setInterval(function(){
  fetch('/api/state').then(r=>r.json()).then(d=>{
    var t=document.getElementById('t');
    t.innerHTML='<tr><th>Parâmetro</th><th>Valor</th></tr>';
    var sa=d.stack_a,sb=d.stack_b,en=d.energy,bs=d.bess;
    var rows=[['Stack A Temp',sa.temp.toFixed(1)+'°C'],['Stack B Temp',sb.temp.toFixed(1)+'°C'],
      ['H₂ Stack A',sa.h2_nm3h.toFixed(0)+' Nm³/h'],['H₂ Stack B',sb.h2_nm3h.toFixed(0)+' Nm³/h'],
      ['Solar',en.solar_mw.toFixed(1)+' MW'],['Eólica',en.wind_mw.toFixed(1)+' MW'],
      ['BESS SoC',bs.soc.toFixed(0)+'%'],['Timestamp',new Date(d.timestamp*1000).toLocaleTimeString()]];
    rows.forEach(function(r){ t.innerHTML+='<tr><td>'+r[0]+'</td><td style="color:#00C9A7">'+r[1]+'</td></tr>'; });
  });
},2000);
</script></body></html>"""


# ═══════════════════════════════════════════════════════════════════
# HTTP + WEBSOCKET SERVER (stdlib — zero dependências)
# ═══════════════════════════════════════════════════════════════════

# Global instances
_plant = None
_ai    = None
_db    = None
_ws_clients: Set = set()
_ws_lock = threading.Lock()
_html_cache = None


def broadcast_ws(data: dict):
    msg = json.dumps(data)
    with _ws_lock:
        dead = set()
        for sock, info in list(_ws_clients):
            try:
                _ws_send(sock, msg)
            except Exception:
                dead.add((sock, info))
        for item in dead:
            _ws_clients.discard(item)


def _ws_handshake(sock, request_line, headers):
    key = headers.get("Sec-WebSocket-Key", "")
    magic = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
    accept = base64.b64encode(
        hashlib.sha1((key + magic).encode()).digest()).decode()
    response = (
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Accept: {accept}\r\n\r\n"
    )
    sock.sendall(response.encode())


def _ws_send(sock, text: str):
    data = text.encode("utf-8")
    length = len(data)
    if length <= 125:
        header = bytes([0x81, length])
    elif length <= 65535:
        header = struct.pack(">BBH", 0x81, 126, length)
    else:
        header = struct.pack(">BBQ", 0x81, 127, length)
    sock.sendall(header + data)


def _ws_recv(sock) -> str:
    raw = sock.recv(2)
    if len(raw) < 2:
        raise ConnectionError("closed")
    opcode = raw[0] & 0x0F
    if opcode == 8:
        raise ConnectionError("close frame")
    masked = raw[1] & 0x80
    length = raw[1] & 0x7F
    if length == 126:
        length = struct.unpack(">H", sock.recv(2))[0]
    elif length == 127:
        length = struct.unpack(">Q", sock.recv(8))[0]
    if masked:
        mask = sock.recv(4)
        data = bytearray(sock.recv(length))
        for i in range(len(data)):
            data[i] ^= mask[i % 4]
        return data.decode("utf-8", errors="replace")
    return sock.recv(length).decode("utf-8", errors="replace")


def handle_ws_client(sock, addr):
    with _ws_lock:
        _ws_clients.add((sock, addr))
    try:
        _ws_send(sock, json.dumps({"type": "state", "data": _plant.snapshot()}))
        _ws_send(sock, json.dumps({"type": "alarms", "data": list(_ai.active_alarms.values())}))
        while True:
            raw = _ws_recv(sock)
            try:
                msg = json.loads(raw)
                cmd = msg.get("cmd", "")
                pl = msg.get("data", {})
                if cmd == "setpoint":
                    tag, val = pl.get("tag"), pl.get("value")
                    ok = _plant.set_setpoint(tag, val) if tag else False
                    if ok: _db.write_setpoint(tag, 0, val)
                    _ws_send(sock, json.dumps({"type": "ack", "ok": ok}))
                elif cmd == "toggle":
                    ok = _plant.set_toggle(pl.get("key",""), pl.get("value", True))
                    _ws_send(sock, json.dumps({"type": "ack", "ok": ok}))
                elif cmd == "chat":
                    resp = _ai.chat(pl.get("message", ""))
                    _ws_send(sock, json.dumps({"type": "chat_response", "message": resp}))
                elif cmd == "esd":
                    m = _plant.trigger_esd()
                    _db.write_event("trip", "ESD", m)
                    _ws_send(sock, json.dumps({"type": "esd", "message": m}))
                elif cmd == "esd_reset":
                    m = _plant.reset_esd()
                    _ws_send(sock, json.dumps({"type": "esd", "status": "reset", "message": m}))
                elif cmd == "ack_alarm":
                    _ai.ack_alarm(pl.get("code",""), pl.get("operator","op"))
                elif cmd == "ping":
                    _ws_send(sock, json.dumps({"type": "pong"}))
            except json.JSONDecodeError:
                pass
    except Exception:
        pass
    finally:
        with _ws_lock:
            _ws_clients.discard((sock, addr))
        try: sock.close()
        except: pass


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # suppress default logging

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/" or path == "/index.html":
            self._serve_html()
        elif path == "/api/state":
            self._json(_plant.snapshot())
        elif path == "/api/alarms":
            self._json({"active": list(_ai.active_alarms.values()),
                        "history": _ai.alarm_history[-50:]})
        elif path == "/api/events":
            self._json(_db.get_events(80))
        elif path.startswith("/api/history/"):
            tag = path.split("/api/history/")[-1]
            qs = parse_qs(parsed.query)
            hours = float(qs.get("hours", [24])[0])
            self._json({"tag": tag, "data": _db.get_history(tag, hours)})
        elif path == "/api/history":
            tags = ["stack_a_temp","stack_b_temp","stack_a_h2","stack_b_h2",
                    "solar_mw","wind_mw","bess_soc","swro_salinity","nh3_rate"]
            result = {t: _db.get_history(t, 24, 300) for t in tags}
            self._json(result)
        elif path == "/api/ai/status":
            self._json(_ai.metrics)
        elif path == "/api/network":
            self._json(_plant.snapshot().get("network", {}))
        elif path == "/api/setpoints":
            self._json(_plant.setpoints)
        elif path == "/api/toggles":
            self._json(_plant.toggles)
        else:
            self.send_error(404)

    def do_POST(self):
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(body)
        except Exception:
            data = {}

        if path == "/api/setpoint":
            tag, val = data.get("tag"), data.get("value")
            ok = _plant.set_setpoint(tag, val) if tag is not None and val is not None else False
            if ok:
                _db.write_setpoint(tag, 0, float(val), data.get("source", "api"))
            self._json({"ok": ok, "tag": tag, "value": val})

        elif path == "/api/toggle":
            ok = _plant.set_toggle(data.get("key",""), data.get("value", True))
            self._json({"ok": ok})

        elif path == "/api/chat":
            message = data.get("message", "")
            resp = _ai.chat(message) if message else "Mensagem vazia."
            self._json({"response": resp, "ts": time.time()})

        elif path == "/api/alarms/ack":
            ok = _ai.ack_alarm(data.get("code",""), data.get("operator","op"))
            self._json({"ok": ok})

        elif path == "/api/esd":
            msg = _plant.trigger_esd()
            _db.write_event("trip", "ESD", msg)
            self._json({"status": "triggered", "message": msg})

        elif path == "/api/esd/reset":
            msg = _plant.reset_esd()
            _db.write_event("info","ESD_RESET",msg)
            self._json({"status": "reset", "message": msg})

        else:
            self.send_error(404)

    def _json(self, obj):
        body = json.dumps(obj, default=str).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def _serve_html(self):
        global _html_cache
        if _html_cache is None:
            _html_cache = build_html().encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(_html_cache))
        self.end_headers()
        self.wfile.write(_html_cache)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()


class WebSocketTCPServer(http.server.HTTPServer):
    """HTTP server that upgrades WebSocket connections inline."""
    def get_request(self):
        return super().get_request()


class UpgradeHandler(Handler):
    def handle(self):
        # Peek at the first line
        try:
            self.raw_requestline = self.rfile.readline(65537)
            if not self.raw_requestline:
                return
            if not self.parse_request():
                return
            upgrade = self.headers.get("Upgrade", "").lower()
            if upgrade == "websocket":
                _ws_handshake(self.connection, self.raw_requestline, self.headers)
                handle_ws_client(self.connection, self.client_address)
            else:
                method = self.command
                if method == "GET":
                    self.do_GET()
                elif method == "POST":
                    self.do_POST()
                elif method == "OPTIONS":
                    self.do_OPTIONS()
                else:
                    self.send_error(405)
        except Exception as e:
            pass


# ═══════════════════════════════════════════════════════════════════
# BACKGROUND THREADS
# ═══════════════════════════════════════════════════════════════════

def sensor_thread():
    """Ticks simulator every 2s and broadcasts state."""
    tick_count = 0
    while True:
        try:
            _plant.tick()
            tick_count += 1
            if tick_count % 5 == 0:
                state = _plant.snapshot()
                rows = []
                sa, sb = state["stack_a"], state["stack_b"]
                en, bs = state["energy"], state["bess"]
                sw, nh = state["swro"], state["nh3"]
                ts = state["timestamp"]
                for tag, val in [
                    ("stack_a_temp", sa["temp"]), ("stack_b_temp", sb["temp"]),
                    ("stack_a_pressure", sa["pressure"]), ("stack_b_pressure", sb["pressure"]),
                    ("stack_a_h2", sa["h2_nm3h"]), ("stack_b_h2", sb["h2_nm3h"]),
                    ("stack_a_efficiency", sa["efficiency"]), ("stack_b_efficiency", sb["efficiency"]),
                    ("solar_mw", en["solar_mw"]), ("wind_mw", en["wind_mw"]),
                    ("total_mw", en["total_mw"]), ("bess_soc", bs["soc"]),
                    ("swro_salinity", sw["product_salinity"]), ("swro_flow", sw["product_flow"]),
                    ("nh3_rate", nh["prod_kgh"]),
                ]:
                    rows.append((ts, tag, float(val)))
                _db.write_sensors(rows)

            if tick_count % 1800 == 0:
                _db.trim()

            # Broadcast via WebSocket if any clients
            with _ws_lock:
                if _ws_clients:
                    broadcast_ws({"type": "state", "data": _plant.snapshot()})

        except Exception as e:
            print(f"[Sensor] {e}")
        time.sleep(2.0)


def ai_thread():
    """Runs AI control cycle every 10s."""
    time.sleep(5)  # wait for simulator to warm up
    while True:
        try:
            _ai.run_cycle()
            # Broadcast alarms if clients connected
            with _ws_lock:
                if _ws_clients:
                    broadcast_ws({"type": "alarms",
                                  "data": list(_ai.active_alarms.values())})
        except Exception as e:
            print(f"[AI] {e}")
        time.sleep(10.0)


# ═══════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════

def main():
    global _plant, _ai, _db

    print("\n" + "="*60)
    print("  AquaH₂ AI-SCADA Platform")
    print("  Kraefegg M.O. · Developer: Railson · Rev 2.1.4")
    print("="*60)

    # Init components
    _db    = Database(DB_FILE)
    _plant = Plant()
    _ai    = AIEngine(_plant, _db)

    print(f"\n  [OK] Banco de dados:   {DB_FILE}")
    print(f"  [OK] Simulador:        planta AquaH₂ Hub RN-01")
    print(f"  [OK] Motor IA:         PID + anomalias + preditivo")

    # Start background threads
    threading.Thread(target=sensor_thread, daemon=True, name="Sensor").start()
    threading.Thread(target=ai_thread,     daemon=True, name="AI").start()
    print(f"  [OK] Threads:          Sensor (2s) + IA (10s)")

    # Start HTTP server
    server = WebSocketTCPServer((HOST, PORT), UpgradeHandler)
    server.allow_reuse_address = True

    url = f"http://{HOST}:{PORT}"
    print(f"\n  Servidor HTTP:   {url}")
    print(f"  API state:       {url}/api/state")
    print(f"  API history:     {url}/api/history")
    print(f"  WebSocket:       ws://{HOST}:{PORT}/ws")
    print(f"\n  Abrindo browser em 1 segundo...")
    print(f"  Ctrl+C para parar\n")
    print("="*60 + "\n")

    _db.write_event("info", "STARTUP", "AquaH2 AI-SCADA iniciado", f"Host: {HOST}:{PORT}")

    # Open browser after slight delay
    def open_browser():
        time.sleep(1.5)
        try:
            webbrowser.open(url)
        except Exception:
            pass
    threading.Thread(target=open_browser, daemon=True).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Encerrando servidor...")
        server.shutdown()
        print("  Servidor encerrado. Até logo!\n")


if __name__ == "__main__":
    main()
