import tempfile
import unittest
from pathlib import Path

from application import Application
from infrastructure.repository import Repository


class ApplicationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.repo=Repository(Path(self.temp.name)/"test.sqlite3")
        self.app=Application(self.repo)

    def tearDown(self): self.temp.cleanup()

    def test_seed_is_persisted_and_dispatch_assigns_real_resources(self):
        state=self.repo.state()
        self.assertEqual(len(state["incidents"]),3)
        result=self.app.run_dispatch()
        self.assertEqual(len(result["accepted"]),3)
        self.assertEqual(len([a for a in self.repo.state()["assignments"] if a["status"]=="active"]),3)
        self.assertEqual(result["findings"],[])

    def test_negotiation_persists_bids(self):
        self.repo.set_algorithm("negotiation")
        self.app.run_dispatch()
        bids=self.repo.state()["bids"]
        self.assertGreater(len(bids),0)
        self.assertIn("accepted",{bid["status"] for bid in bids})

    def test_duplicate_event_key_is_idempotent(self):
        before=self.app.inject_event("bridge_closure",{"edge_id":"e3"},"close-one")
        after=self.app.inject_event("bridge_closure",{"edge_id":"e3"},"close-one")
        self.assertEqual(len(before["events"]),len(after["events"]))

    def test_triple_disruption_changes_real_state_and_audits(self):
        self.repo.reset("triple-disruption")
        self.repo.set_algorithm("centralized")
        initial=self.app.run_dispatch()
        self.assertEqual(len(initial["accepted"]),3)
        self.app.toggle("running")
        result=self.app.advance()
        state=result["state"]
        self.assertEqual(state["session"]["tick"],1)
        self.assertEqual(next(e for e in state["edges"] if e["id"]=="e3")["status"],"closed")
        self.assertEqual(next(r for r in state["resources"] if r["id"]=="B-02")["status"],"failed")
        self.assertEqual(next(s for s in state["shelters"] if s["id"]=="SH-02")["occupancy"],10)
        self.assertTrue(all(event["processed"] for event in state["events"]))
        self.assertTrue(any(assignment["status"]=="cancelled" for assignment in state["assignments"]))
        self.assertTrue(any("Assignment accepted" in decision for decision in result["decisions"]))
        self.assertEqual(len([assignment for assignment in state["assignments"] if assignment["status"]=="active"]),3)
        replacement=next(assignment for assignment in state["assignments"] if assignment["incident_id"]=="I-101" and assignment["status"]=="active")
        self.assertEqual(replacement["resource_id"],"B-05")
        rerouted=next(assignment for assignment in state["assignments"] if assignment["incident_id"]=="I-102" and assignment["status"]=="active")
        route=__import__("json").loads(rerouted["route"])
        self.assertFalse(any(set(edge)=={"n2","n3"} for edge in zip(route,route[1:])))
        self.assertTrue(any("rerouted" in decision for decision in result["decisions"]))
        self.assertEqual(state["audits"][-1]["passed"],1)

    def test_invalid_incident_is_rejected(self):
        with self.assertRaises(ValueError):
            self.app.create_incident({"title":"x","node_id":"base","category":"medical","severity":9,"urgency":1,"people":2})

    def test_simulation_ticks_move_resources_and_resolve_incidents(self):
        self.app.run_dispatch()
        self.app.toggle("running")
        for _ in range(80):
            state=self.app.advance()["state"]
            if all(incident["status"]=="resolved" for incident in state["incidents"]):
                break
        self.assertTrue(all(incident["status"]=="resolved" for incident in state["incidents"]))
        self.assertTrue(all(resource["status"]=="available" for resource in state["resources"]))
        with self.repo.connect() as con:
            self.assertIsNotNone(con.execute("SELECT 1 FROM snapshots WHERE session_id='session-demo' AND tick=?",(state["session"]["tick"],)).fetchone())


if __name__ == "__main__": unittest.main()
