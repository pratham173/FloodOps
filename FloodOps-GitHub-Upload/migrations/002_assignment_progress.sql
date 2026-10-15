ALTER TABLE assignments ADD COLUMN route_step INTEGER NOT NULL DEFAULT 0 CHECK(route_step >= 0);
ALTER TABLE assignments ADD COLUMN edge_progress INTEGER NOT NULL DEFAULT 0 CHECK(edge_progress >= 0);
