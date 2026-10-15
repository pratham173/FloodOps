"""Application use cases. Mutations are kept outside presentation code."""
from __future__ import annotations

import json
from contextlib import closing
from typing import Any

from domain.engine import audit, plan, route
from infrastructure.repository import Repository, new_id, now


class Application:
    def __init__(self, repository: Repository):
        self.repo = repository

    def run_dispatch(self) -> dict[str, Any]:
        with closing(self.repo.connect()) as con:
            con.execute("BEGIN IMMEDIATE")
            state = self.repo._state(con, "session-demo")
            algorithm = state["session"]["algorithm"]
            result = plan(state, algorithm)
            stamp, tick = now(), state["session"]["tick"]
            resource_used: set[str] = set()
            incident_used: set[str] = set()
            accepted: list[str] = []
            for candidate in result["assignments"]:
                incident, resource = candidate["incident"], candidate["resource"]
                if resource["id"] in resource_used or incident["id"] in incident_used:
                    continue
                current = con.execute("SELECT status FROM resources WHERE id=?", (resource["id"],)).fetchone()
                current_incident = con.execute("SELECT status FROM incidents WHERE id=?", (incident["id"],)).fetchone()
                if not current or current["status"] != "available" or not current_incident or current_incident["status"] not in {"pending", "verified"}:
                    continue
                assignment_id = new_id("ASN")
                con.execute("INSERT INTO assignments (id,session_id,incident_id,resource_id,shelter_id,status,route,travel_cost,reason,created_tick,ended_tick) VALUES(?,?,?,?,NULL,'active',?,?,?,?,NULL)", (assignment_id,"session-demo",incident["id"],resource["id"],json.dumps(candidate["path"]),candidate["cost"],f"{algorithm} selected a feasible safe route; priority {incident['severity']}/5",tick))
                con.execute("UPDATE incidents SET status='assigned' WHERE id=?", (incident["id"],))
                con.execute("UPDATE resources SET status='assigned',assignment_id=? WHERE id=?", (assignment_id,resource["id"]))
                accepted.append(assignment_id)
                resource_used.add(resource["id"])
                incident_used.add(incident["id"])
            for bid in result["bids"]:
                con.execute("INSERT INTO bids VALUES(?,?,?,?,?,?,?,?)", (new_id("BID"),"session-demo",bid["incident_id"],bid["resource_id"],bid["score"],bid["status"],bid["reason"],tick))
            updated = self.repo._state(con,"session-demo")
            findings = audit(updated)
            con.execute("INSERT INTO audits VALUES(?,?,?,?,?,?)", (new_id("AUD"),"session-demo",tick,int(not findings),json.dumps(findings),stamp))
            con.execute("UPDATE sessions SET updated_at=? WHERE id='session-demo'", (stamp,))
            con.execute("COMMIT")
        return {"accepted": accepted, "findings": findings, "state": self.repo.state()}

    def advance(self) -> dict[str, Any]:
        with closing(self.repo.connect()) as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT tick,status FROM sessions WHERE id='session-demo'").fetchone()
            tick = row["tick"] + 1
            con.execute("UPDATE sessions SET tick=?,updated_at=? WHERE id='session-demo'", (tick,now()))
            due = con.execute("SELECT * FROM events WHERE session_id='session-demo' AND tick<=? AND processed=0 ORDER BY tick,sequence,id", (tick,)).fetchall()
            decisions: list[str] = []
            for event in due:
                payload = json.loads(event["payload"])
                if event["kind"] == "bridge_closure":
                    con.execute("UPDATE edges SET status='closed' WHERE id=? AND session_id='session-demo'", (payload["edge_id"],))
                    decisions.append(f"Bridge {payload['edge_id']} closed; impacted routes are rechecked")
                elif event["kind"] == "resource_failure":
                    con.execute("UPDATE resources SET status='failed' WHERE id=? AND session_id='session-demo'", (payload["resource_id"],))
                    decisions.append(f"Resource {payload['resource_id']} failed")
                    assignments = con.execute("SELECT * FROM assignments WHERE resource_id=? AND status='active'", (payload["resource_id"],)).fetchall()
                    for assignment in assignments:
                        con.execute("UPDATE assignments SET status='cancelled',ended_tick=? WHERE id=?", (tick,assignment["id"]))
                        con.execute("UPDATE incidents SET status='pending' WHERE id=?", (assignment["incident_id"],))
                        decisions.append(f"Assignment {assignment['id']} cancelled; reservation released")
                elif event["kind"] == "shelter_occupancy":
                    shelter = con.execute("SELECT capacity FROM shelters WHERE id=?", (payload["shelter_id"],)).fetchone()
                    if shelter and 0 <= payload["occupancy"] <= shelter["capacity"]:
                        con.execute("UPDATE shelters SET occupancy=? WHERE id=?", (payload["occupancy"],payload["shelter_id"]))
                        decisions.append(f"{payload['shelter_id']} occupancy updated to {payload['occupancy']}")
                con.execute("UPDATE events SET processed=1 WHERE id=?", (event["id"],))
            if due:
                # Revalidate every active commitment against the combined post-event graph.
                post_event = self.repo._state(con, "session-demo")
                resources = {resource["id"]: resource for resource in post_event["resources"]}
                incidents = {incident["id"]: incident for incident in post_event["incidents"]}
                for assignment in list(post_event["assignments"]):
                    if assignment["status"] != "active":
                        continue
                    resource = resources.get(assignment["resource_id"])
                    incident = incidents.get(assignment["incident_id"])
                    found = route(post_event["nodes"], post_event["edges"], resource["node_id"], incident["node_id"]) if resource and incident and resource["status"] != "failed" else None
                    if found is None:
                        con.execute("UPDATE assignments SET status='cancelled',ended_tick=? WHERE id=?", (tick,assignment["id"]))
                        if resource and resource["status"] != "failed":
                            con.execute("UPDATE resources SET status='available',assignment_id=NULL WHERE id=?", (resource["id"],))
                        if incident:
                            con.execute("UPDATE incidents SET status='pending' WHERE id=?", (incident["id"],))
                        decisions.append(f"Assignment {assignment['id']} cancelled; no safe feasible route remains")
                    else:
                        path, cost = found
                        if path != json.loads(assignment["route"]):
                            con.execute("UPDATE assignments SET route=?,travel_cost=?,route_step=0,edge_progress=0 WHERE id=?", (json.dumps(path),cost,assignment["id"]))
                            decisions.append(f"Assignment {assignment['id']} rerouted at cost {cost}")
            # Each simulation tick advances movement along one edge. Edge weights are
            # elapsed simulation ticks; resources move only after an edge is traversed.
            movement_state = self.repo._state(con, "session-demo")
            nodes = {node["id"]: node for node in movement_state["nodes"]}
            edges = movement_state["edges"]
            resource_by_id = {resource["id"]: resource for resource in movement_state["resources"]}
            incident_by_id = {incident["id"]: incident for incident in movement_state["incidents"]}
            for assignment in movement_state["assignments"]:
                if assignment["status"] != "active":
                    continue
                resource = resource_by_id.get(assignment["resource_id"])
                incident = incident_by_id.get(assignment["incident_id"])
                path = json.loads(assignment["route"])
                if not resource or not incident:
                    continue
                if resource["node_id"] == incident["node_id"]:
                    con.execute("UPDATE assignments SET status='completed',ended_tick=? WHERE id=?", (tick,assignment["id"]))
                    con.execute("UPDATE incidents SET status='resolved' WHERE id=?", (incident["id"],))
                    con.execute("UPDATE resources SET status='available',assignment_id=NULL WHERE id=?", (resource["id"],))
                    decisions.append(f"Incident {incident['id']} resolved by {resource['id']}")
                    continue
                step = int(assignment["route_step"])
                if step + 1 >= len(path) or path[step] != resource["node_id"]:
                    continue
                link = next((edge for edge in edges if {edge["source"],edge["target"]} == {path[step],path[step+1]}),None)
                if not link or link["status"] != "open" or link["safety"] != "safe":
                    continue
                progress = int(assignment["edge_progress"]) + 1
                if progress >= int(link["cost"]):
                    next_node = path[step+1]
                    next_step, progress = step+1, 0
                    con.execute("UPDATE resources SET node_id=?,status='rescuing' WHERE id=?", (next_node,resource["id"]))
                    decisions.append(f"Resource {resource['id']} reached {nodes[next_node]['label']}")
                else:
                    next_node, next_step = resource["node_id"], step
                    con.execute("UPDATE resources SET status='travelling' WHERE id=?", (resource["id"],))
                con.execute("UPDATE assignments SET route_step=?,edge_progress=? WHERE id=?", (next_step,progress,assignment["id"]))
            tick_state = self.repo._state(con, "session-demo")
            tick_findings = audit(tick_state)
            con.execute("INSERT INTO audits VALUES(?,?,?,?,?,?)", (new_id("AUD"),"session-demo",tick,int(not tick_findings),json.dumps(tick_findings),now()))
            # All same-tick environment changes are applied before plans are re-evaluated.
            con.execute("COMMIT")
        state = self.repo.state()
        if due:
            dispatch = self.run_dispatch()
            state = dispatch["state"]
            decisions.extend(f"Assignment accepted: {aid}" for aid in dispatch["accepted"])
            decisions.extend(dispatch["findings"])
        state = self.repo.state()
        with closing(self.repo.connect()) as con:
            con.execute("INSERT OR REPLACE INTO snapshots VALUES(?,?,?,?)", (new_id("SNP"),"session-demo",tick,json.dumps(state,sort_keys=True)))
        return {"tick": tick, "decisions": decisions, "state": state}

    def toggle(self, status: str) -> dict[str, Any]:
        if status not in {"running", "paused"}:
            raise ValueError("Invalid simulation state")
        with closing(self.repo.connect()) as con:
            con.execute("UPDATE sessions SET status=?,updated_at=? WHERE id='session-demo'", (status,now()))
        return self.repo.state()

    def inject_event(self, kind: str, payload: dict[str, Any], idempotency_key: str) -> dict[str, Any]:
        if kind not in {"bridge_closure","resource_failure","shelter_occupancy"}:
            raise ValueError("Unsupported event type")
        if not isinstance(payload, dict):
            raise ValueError("Event payload must be an object")
        required = {"bridge_closure": ("edge_id",), "resource_failure": ("resource_id",), "shelter_occupancy": ("shelter_id","occupancy")}[kind]
        if not all(key in payload for key in required):
            raise ValueError("Event payload is incomplete")
        with closing(self.repo.connect()) as con:
            con.execute("BEGIN IMMEDIATE")
            exists = con.execute("SELECT id FROM events WHERE session_id='session-demo' AND idempotency_key=?", (idempotency_key,)).fetchone()
            if exists:
                con.execute("COMMIT")
                return self.repo.state()
            tick = con.execute("SELECT tick FROM sessions WHERE id='session-demo'").fetchone()[0] + 1
            for key, table in [("edge_id","edges"),("resource_id","resources"),("shelter_id","shelters")]:
                if key in payload and not con.execute(f"SELECT 1 FROM {table} WHERE id=? AND session_id='session-demo'", (payload[key],)).fetchone():
                    raise ValueError(f"Unknown {key}")
            if "occupancy" in payload and (not isinstance(payload["occupancy"],int) or isinstance(payload["occupancy"],bool) or payload["occupancy"] < 0):
                raise ValueError("Occupancy must be a non-negative integer")
            if kind == "shelter_occupancy":
                capacity = con.execute("SELECT capacity FROM shelters WHERE id=?", (payload["shelter_id"],)).fetchone()[0]
                if payload["occupancy"] > capacity:
                    raise ValueError("Occupancy cannot exceed the shelter's configured capacity")
            seq = con.execute("SELECT COALESCE(MAX(sequence),0)+1 FROM events WHERE session_id='session-demo' AND tick=?", (tick,)).fetchone()[0]
            event_id = new_id("EVT")
            con.execute("INSERT INTO events VALUES(?,?,?,?,?,?,?,?,?)", (event_id,"session-demo",idempotency_key,tick,seq,kind,json.dumps(payload),0,now()))
            con.execute("COMMIT")
        return self.repo.state()

    def create_incident(self, payload: dict[str, Any]) -> dict[str, Any]:
        required = {"title","node_id","severity","urgency","people","category"}
        if not required.issubset(payload):
            raise ValueError("Incident fields are incomplete")
        if not isinstance(payload["title"],str) or not 3 <= len(payload["title"].strip()) <= 100:
            raise ValueError("Title must be 3–100 characters")
        for field in ("severity","urgency"):
            if not isinstance(payload[field],int) or not 1 <= payload[field] <= 5:
                raise ValueError(f"{field} must be an integer from 1 to 5")
        if not isinstance(payload["people"],int) or isinstance(payload["people"],bool) or not 1 <= payload["people"] <= 100:
            raise ValueError("People must be an integer from 1 to 100")
        if payload["category"] not in {"trapped_residents","medical","stranded_family","evacuation","supplies"}:
            raise ValueError("Choose a supported incident category")
        confidence=payload.get("confidence",.7)
        if not isinstance(confidence,(int,float)) or isinstance(confidence,bool) or not 0 <= confidence <= 1:
            raise ValueError("Confidence must be between 0 and 1")
        with closing(self.repo.connect()) as con:
            if not con.execute("SELECT 1 FROM nodes WHERE id=? AND session_id='session-demo'", (payload["node_id"],)).fetchone():
                raise ValueError("Choose a known network node")
            tick = con.execute("SELECT tick FROM sessions WHERE id='session-demo'").fetchone()[0]
            incident_id = new_id("I")
            con.execute("INSERT INTO incidents VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (incident_id,"session-demo",payload["node_id"],None,payload["title"].strip(),payload["category"],payload["severity"],payload["urgency"],payload["people"],"pending",float(confidence),tick))
        return self.repo.state()

    def fail_resource(self, resource_id: str) -> dict[str, Any]:
        return self.inject_event("resource_failure", {"resource_id":resource_id}, f"manual-failure-{resource_id}-{self.repo.state()['session']['tick']+1}")

