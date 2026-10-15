"""SQLite persistence adapter with explicit transaction boundaries."""
from __future__ import annotations

import json
import os
import random
import sqlite3
import uuid
from datetime import datetime, timezone
from contextlib import closing
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = Path(os.environ.get("FLOODOPS_DB_PATH", ROOT / "data" / "floodops.sqlite3"))


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10].upper()}"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class Repository:
    def __init__(self, path: Path = DB_PATH):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _initialize(self) -> None:
        with closing(self.connect()) as con:
            con.executescript((ROOT / "migrations" / "001_initial.sql").read_text())
            if con.execute("PRAGMA user_version").fetchone()[0] < 2:
                con.executescript((ROOT / "migrations" / "002_assignment_progress.sql").read_text())
                con.execute("PRAGMA user_version=2")
            if con.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0:
                con.execute("BEGIN IMMEDIATE")
                try:
                    self._seed(con)
                    con.execute("COMMIT")
                except Exception:
                    con.execute("ROLLBACK")
                    raise

    def _seed(self, con: sqlite3.Connection) -> None:
        stamp = now()
        con.execute("INSERT INTO profiles VALUES(?,?,?)", ("profile-local", "Local operator", stamp))
        con.execute("INSERT INTO projects VALUES(?,?,?,?)", ("project-demo", "profile-local", "Synthetic response lab", stamp))
        con.execute("INSERT INTO sessions VALUES(?,?,?,?,?,?,?,?,?,?)", ("session-demo", "project-demo", "Baseline Flood Response", "baseline", 20261015, "centralized", 0, "paused", stamp, stamp))
        self._seed_session_data(con, "session-demo")

    def _seed_session_data(self, con: sqlite3.Connection, sid: str) -> None:
        seed = int(con.execute("SELECT seed FROM sessions WHERE id=?", (sid,)).fetchone()[0])
        rng = random.Random(seed)
        neighborhoods = [("north", "Northbank", 2, 1), ("market", "Old Market", 5, 2), ("south", "South Ward", 3, 5)]
        con.executemany("INSERT INTO neighborhoods VALUES(?,?,?,?,?)", [(f"{sid}-hood-{n}", sid, name, x, y) for n,name,x,y in neighborhoods])
        nodes = [
            ("base", "Rescue base", "base", 0, 2, "north"), ("n1", "North Bridge Approach", "intersection", 2, 2, "north"),
            ("n2", "Market Square", "intersection", 4, 2, "market"), ("n3", "East Bridge", "bridge", 6, 2, "market"),
            ("n4", "South Crossing", "intersection", 6, 4, "south"), ("n5", "South Ward", "neighborhood", 3, 5, "south"),
            ("shelter-a", "Civic Hall Shelter", "shelter", 1, 5, "south"), ("shelter-b", "Hill School Shelter", "shelter", 7, 5, "south"),
            ("incident-a", "Riverside Apartments", "incident", 5, 1, "north"), ("incident-b", "Clinic Evacuation Point", "incident", 7, 3, "market"),
            ("incident-c", "South Ward Homes", "incident", 3, 4, "south"),
        ]
        con.executemany("INSERT INTO nodes VALUES(?,?,?,?,?,?,?)", [(nid,sid, f"{sid}-hood-{hood}",label,kind,x,y) for nid,label,kind,x,y,hood in nodes])
        edges = [("e1","base","n1",2),("e2","n1","n2",2),("e3","n2","n3",2),("e4","n3","n4",2),("e5","n4","n5",3),("e6","n5","shelter-a",3),("e7","n4","shelter-b",2),("e8","n2","incident-a",2),("e9","n3","incident-b",2),("e10","n5","incident-c",1),("e11","n1","shelter-a",5),("e12","n2","n5",4),("e13","n3","shelter-b",3)]
        con.executemany("INSERT INTO edges VALUES(?,?,?,?,?,?,'open','safe',1)", [(eid,sid,a,b,c,"all") for eid,a,b,c in edges])
        incidents = [("I-101","incident-a","hood-north","Trapped residents · Riverside", "trapped_residents",5,4,3),("I-102","incident-b","hood-market","Urgent clinic evacuation", "medical",5,5,2),("I-103","incident-c","hood-south","Stranded family · South Ward", "stranded_family",3,3,4)]
        con.executemany("INSERT INTO incidents VALUES(?,?,?,?,?,?,?,?,?,?,?,0)", [(iid,sid,node,f"{sid}-{hood}",title,cat,sev,urg,people,"pending",round(rng.uniform(.72,.98),2)) for iid,node,hood,title,cat,sev,urg,people in incidents])
        resources = [("B-01","Swiftwater Alpha","base",6,"water_rescue,medical"),("B-02","Swiftwater Bravo","n1",5,"water_rescue"),("B-03","Medical Evacuation","n5",4,"medical,evacuation"),("B-04","Reserve Medical","n1",6,"medical,evacuation"),("B-05","Reserve Swiftwater","base",6,"water_rescue")]
        con.executemany("INSERT INTO resources VALUES(?,?,?,?,?,?,?,NULL)", [(rid,sid,label,node,capacity,caps,"available") for rid,label,node,capacity,caps in resources])
        con.executemany("INSERT INTO shelters VALUES(?,?,?,?,?,?,1,'open')", [("SH-01",sid,"shelter-a","Civic Hall Shelter",14,5),("SH-02",sid,"shelter-b","Hill School Shelter",10,6)])
        con.execute("INSERT INTO audits VALUES(?,?,?,?,?,?)", (new_id("AUD"),sid,0,1,"[]",now()))
        con.execute("INSERT INTO snapshots VALUES(?,?,?,?)", (new_id("SNP"),sid,0,json.dumps(self._state(con,sid),sort_keys=True)))

    def sessions(self) -> list[dict[str, Any]]:
        with closing(self.connect()) as con:
            return [dict(r) for r in con.execute("SELECT * FROM sessions ORDER BY created_at")]

    def state(self, session_id: str = "session-demo") -> dict[str, Any]:
        with closing(self.connect()) as con:
            if not con.execute("SELECT 1 FROM sessions WHERE id=?", (session_id,)).fetchone():
                raise KeyError("Simulation session not found")
            return self._state(con, session_id)

    def _state(self, con: sqlite3.Connection, sid: str) -> dict[str, Any]:
        def rows(query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
            return [dict(r) for r in con.execute(query, params)]
        session = dict(con.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone())
        state = {"session": session}
        for key, table, order in [("nodes","nodes","id"),("edges","edges","id"),("neighborhoods","neighborhoods","id"),("incidents","incidents","id"),("resources","resources","id"),("shelters","shelters","id"),("assignments","assignments","created_tick,id"),("bids","bids","tick,id"),("events","events","tick,sequence,id"),("audits","audits","tick,created_at")]:
            state[key] = rows(f"SELECT * FROM {table} WHERE session_id=? ORDER BY {order}", (sid,))
        for row in state["nodes"]:
            hood = next((h["name"] for h in state["neighborhoods"] if h["id"] == row["neighborhood_id"]), "")
            row["neighborhood"] = hood
        for row in state["incidents"]:
            row["priority"] = 10 * row["severity"] + 6 * row["urgency"] + row["people"] + .5 * max(0, session["tick"] - row["created_tick"])
        return state

    def reset(self, scenario: str = "baseline", seed: int = 20261015) -> dict[str, Any]:
        if scenario not in {"baseline", "triple-disruption"}:
            raise ValueError("Unsupported scenario")
        if not isinstance(seed, int) or isinstance(seed, bool) or not 1 <= seed <= 2_147_483_647:
            raise ValueError("Seed must be an integer from 1 to 2147483647")
        with closing(self.connect()) as con:
            con.execute("BEGIN IMMEDIATE")
            old = con.execute("SELECT id FROM sessions WHERE id='session-demo'").fetchone()
            if old:
                con.execute("DELETE FROM sessions WHERE id='session-demo'")
            stamp = now()
            sid = "session-demo"
            con.execute("INSERT INTO sessions VALUES(?,?,?,?,?,?,?,?,?,?)", (sid,"project-demo", "Triple Disruption Demonstration" if scenario == "triple-disruption" else "Baseline Flood Response", scenario, seed, "centralized", 0, "paused", stamp, stamp))
            self._seed_session_data(con, sid)
            if scenario == "triple-disruption":
                for sequence, (key, kind, payload) in enumerate([("triple-bridge","bridge_closure",{"edge_id":"e3"}),("triple-boat","resource_failure",{"resource_id":"B-02"}),("triple-shelter","shelter_occupancy",{"shelter_id":"SH-02","occupancy":10})], start=1):
                    con.execute("INSERT INTO events VALUES(?,?,?,?,?,?,?,?,?)", (key,sid,key,1,sequence,kind,json.dumps(payload),0,stamp))
            con.execute("COMMIT")
        return self.state()

    def set_algorithm(self, algorithm: str) -> None:
        if algorithm not in {"centralized", "negotiation"}:
            raise ValueError("Choose centralized or negotiation")
        with closing(self.connect()) as con:
            con.execute("UPDATE sessions SET algorithm=?,updated_at=? WHERE id='session-demo'", (algorithm, now()))

