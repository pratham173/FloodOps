"""Pure deterministic flood response policies and safety checks."""
from __future__ import annotations

import heapq
from typing import Any


def route(nodes: list[dict[str, Any]], edges: list[dict[str, Any]], start: str,
          goal: str, safe_only: bool = True) -> tuple[list[str], int] | None:
    """Return the lowest-cost feasible path; ties use stable node IDs."""
    node_ids = {node["id"] for node in nodes}
    if start not in node_ids or goal not in node_ids:
        return None
    graph: dict[str, list[tuple[int, str]]] = {node_id: [] for node_id in node_ids}
    for edge in edges:
        if edge["status"] != "open" or (safe_only and edge["safety"] != "safe"):
            continue
        graph[edge["source"]].append((int(edge["cost"]), edge["target"]))
        graph[edge["target"]].append((int(edge["cost"]), edge["source"]))
    costs = {start: 0}
    previous: dict[str, str] = {}
    queue: list[tuple[int, str]] = [(0, start)]
    while queue:
        cost, node_id = heapq.heappop(queue)
        if cost != costs.get(node_id):
            continue
        if node_id == goal:
            path = [goal]
            while path[-1] != start:
                path.append(previous[path[-1]])
            return list(reversed(path)), cost
        for edge_cost, neighbor in sorted(graph[node_id], key=lambda item: item[1]):
            candidate = cost + edge_cost
            if candidate < costs.get(neighbor, 1 << 60):
                costs[neighbor] = candidate
                previous[neighbor] = node_id
                heapq.heappush(queue, (candidate, neighbor))
    return None


def priority(incident: dict[str, Any], tick: int) -> float:
    """Score = 10×severity + 6×urgency + vulnerable people + 0.5×wait ticks."""
    wait = max(0, tick - int(incident.get("created_tick", 0)))
    return 10 * int(incident["severity"]) + 6 * int(incident["urgency"]) + int(incident["people"]) + 0.5 * wait


def required_capability(category: str) -> str:
    return {"medical":"medical", "evacuation":"evacuation", "supplies":"supplies"}.get(category, "water_rescue")


def plan(state: dict[str, Any], algorithm: str) -> dict[str, Any]:
    """Produce assignments and negotiation records without mutating state."""
    incidents = [i for i in state["incidents"] if i["status"] in {"pending", "verified"}]
    resources = [r for r in state["resources"] if r["status"] == "available"]
    candidates: list[dict[str, Any]] = []
    for incident in incidents:
        for resource in resources:
            capabilities = set(str(resource.get("capabilities", "")).split(","))
            if required_capability(incident["category"]) not in capabilities:
                continue
            if resource["capacity"] < incident["people"]:
                continue
            found = route(state["nodes"], state["edges"], resource["node_id"], incident["node_id"])
            if found is None:
                continue
            path, cost = found
            score = priority(incident, state["session"]["tick"]) - cost
            candidates.append({"incident": incident, "resource": resource, "path": path,
                               "cost": cost, "score": score})
    chosen: list[dict[str, Any]] = []
    bids: list[dict[str, Any]] = []
    if algorithm == "centralized":
        # Deterministic greedy heuristic; it does not claim global optimality.
        candidates.sort(key=lambda c: (-priority(c["incident"], state["session"]["tick"]), c["cost"],
                                       c["incident"]["id"], c["resource"]["id"]))
        used_incidents: set[str] = set()
        used_resources: set[str] = set()
        for candidate in candidates:
            iid, rid = candidate["incident"]["id"], candidate["resource"]["id"]
            if iid not in used_incidents and rid not in used_resources:
                chosen.append(candidate)
                used_incidents.add(iid)
                used_resources.add(rid)
    elif algorithm == "negotiation":
        candidates.sort(key=lambda c: (-c["score"], c["resource"]["id"], c["incident"]["id"]))
        by_incident: dict[str, list[dict[str, Any]]] = {}
        for candidate in candidates:
            iid = candidate["incident"]["id"]
            by_incident.setdefault(iid, []).append(candidate)
            bids.append({"incident_id": iid, "resource_id": candidate["resource"]["id"],
                         "score": candidate["score"], "status": "proposed", "reason": "Feasible route, capacity, and availability"})
        used_resources: set[str] = set()
        incident_order = sorted(by_incident, key=lambda iid: (-by_incident[iid][0]["score"],iid))
        for iid in incident_order:
            candidate = next((item for item in by_incident[iid] if item["resource"]["id"] not in used_resources),None)
            if candidate:
                chosen.append(candidate)
                used_resources.add(candidate["resource"]["id"])
        winners = {candidate["incident"]["id"]: candidate["resource"]["id"] for candidate in chosen}
        for bid in bids:
            winner = winners.get(bid["incident_id"])
            if winner == bid["resource_id"]:
                bid["status"] = "accepted"
            else:
                bid["status"] = "rejected"
                if bid["reason"] == "Feasible route, capacity, and availability":
                    bid["reason"] = "Resource committed to a higher-ranked request" if bid["resource_id"] in used_resources else "Proposal ranked below the accepted bid"
    else:
        raise ValueError("algorithm must be centralized or negotiation")
    return {"assignments": chosen, "bids": bids}


def audit(state: dict[str, Any]) -> list[str]:
    """Return invariant violations for active assignments and shelter capacity."""
    findings: list[str] = []
    resources = {r["id"]: r for r in state["resources"]}
    incidents = {i["id"]: i for i in state["incidents"]}
    seen_resources: set[str] = set()
    for assignment in state["assignments"]:
        if assignment["status"] != "active":
            continue
        rid, iid = assignment["resource_id"], assignment["incident_id"]
        resource = resources.get(rid)
        incident = incidents.get(iid)
        if not resource or resource["status"] == "failed":
            findings.append(f"Active assignment {assignment['id']} uses missing or failed resource {rid}")
        if rid in seen_resources:
            findings.append(f"Resource {rid} has conflicting active assignments")
        seen_resources.add(rid)
        if not incident:
            findings.append(f"Assignment {assignment['id']} references missing incident {iid}")
        elif not route(state["nodes"], state["edges"], resource["node_id"], incident["node_id"]):
            findings.append(f"Assignment {assignment['id']} has no safe feasible route")
    reservations: dict[str, int] = {}
    for assignment in state["assignments"]:
        if assignment["status"] == "active" and assignment.get("shelter_id"):
            incident = incidents.get(assignment["incident_id"])
            if incident:
                shelter_id = assignment["shelter_id"]
                reservations[shelter_id] = reservations.get(shelter_id, 0) + int(incident["people"])
    for shelter in state["shelters"]:
        if shelter["occupancy"] + reservations.get(shelter["id"], 0) > shelter["capacity"]:
            findings.append(f"Shelter {shelter['id']} exceeds capacity")
    return findings
