import unittest

from domain.engine import audit, plan, priority, route


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.nodes = [{"id": key} for key in "abcd"]
        self.edges = [
            {"source":"a","target":"b","cost":2,"status":"open","safety":"safe"},
            {"source":"b","target":"d","cost":3,"status":"open","safety":"safe"},
            {"source":"a","target":"c","cost":1,"status":"open","safety":"unsafe"},
            {"source":"c","target":"d","cost":1,"status":"open","safety":"safe"},
        ]

    def test_shortest_safe_route_excludes_unsafe_edges(self):
        self.assertEqual(route(self.nodes,self.edges,"a","d"), (["a","b","d"],5))

    def test_closed_edge_yields_typed_no_route(self):
        for edge in self.edges[:2]: edge["status"]="closed"
        self.assertIsNone(route(self.nodes,self.edges,"a","d"))

    def test_priority_increases_with_wait(self):
        incident={"severity":4,"urgency":3,"people":2,"created_tick":2}
        self.assertEqual(priority(incident,6)-priority(incident,2),2)

    def test_dispatch_tie_break_is_reproducible(self):
        state={"session":{"tick":0},"nodes":[{"id":"base"},{"id":"incident"}],"edges":[{"source":"base","target":"incident","cost":1,"status":"open","safety":"safe"}],"incidents":[{"id":"I-1","node_id":"incident","category":"trapped_residents","severity":3,"urgency":3,"people":1,"status":"pending","created_tick":0}],"resources":[{"id":"B-02","node_id":"base","capacity":2,"status":"available","capabilities":"water_rescue"},{"id":"B-01","node_id":"base","capacity":2,"status":"available","capabilities":"water_rescue"}]}
        result=plan(state,"negotiation")
        self.assertEqual(result["assignments"][0]["resource"]["id"],"B-01")
        self.assertEqual(result,plan(state,"negotiation"))

    def test_negotiation_rejects_conflict_and_assigns_next_available_resource(self):
        state={"session":{"tick":0},"nodes":[{"id":"base"},{"id":"a"},{"id":"b"}],"edges":[{"source":"base","target":"a","cost":1,"status":"open","safety":"safe"},{"source":"base","target":"b","cost":1,"status":"open","safety":"safe"}],"incidents":[{"id":"I-1","node_id":"a","category":"trapped_residents","severity":5,"urgency":5,"people":1,"status":"pending","created_tick":0},{"id":"I-2","node_id":"b","category":"trapped_residents","severity":2,"urgency":2,"people":1,"status":"pending","created_tick":0}],"resources":[{"id":"B-1","node_id":"base","capacity":2,"status":"available","capabilities":"water_rescue"},{"id":"B-2","node_id":"base","capacity":2,"status":"available","capabilities":"water_rescue"}]}
        result=plan(state,"negotiation")
        self.assertEqual(len(result["assignments"]),2)
        decisions={bid["incident_id"]:bid["status"] for bid in result["bids"] if bid["resource_id"]=="B-1"}
        self.assertEqual(decisions["I-1"],"accepted")
        self.assertEqual(decisions["I-2"],"rejected")

    def test_audit_detects_failed_active_resource(self):
        state={"nodes":[{"id":"a"},{"id":"b"}],"edges":[{"source":"a","target":"b","cost":1,"status":"open","safety":"safe"}],"resources":[{"id":"r","node_id":"a","status":"failed"}],"incidents":[{"id":"i","node_id":"b"}],"assignments":[{"id":"x","resource_id":"r","incident_id":"i","status":"active"}],"shelters":[]}
        self.assertTrue(any("failed resource" in item for item in audit(state)))


if __name__ == "__main__": unittest.main()
