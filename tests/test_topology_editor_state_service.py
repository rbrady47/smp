import os
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("DATABASE_URL", "sqlite:///smp-test.db")

from app.db import Base
from app.schemas import TopologyEditorStateUpdate
from app.topology_editor_state_service import (
    get_topology_editor_state_payload,
    upsert_topology_editor_state,
)


class TopologyEditorStateServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=engine)
        self.session = sessionmaker(bind=engine, autoflush=False, autocommit=False)()

    def tearDown(self) -> None:
        self.session.close()

    def test_get_returns_empty_payload_when_state_not_created(self) -> None:
        payload = get_topology_editor_state_payload(self.session)

        self.assertEqual(payload["scope"], "default")
        self.assertFalse(payload["exists"])
        self.assertEqual(payload["layout_overrides"], {})
        self.assertIsNone(payload["state_log_layout"])
        self.assertEqual(payload["link_anchor_assignments"], {})

    def test_upsert_round_trips_layout_log_and_link_assignments(self) -> None:
        saved = upsert_topology_editor_state(
            TopologyEditorStateUpdate(
                layout_overrides={
                    "lvl0-cloud": {"x": 320, "y": 180, "size": 156},
                    "lvl2-div-hq": {"x": 200, "y": 720, "size": 148},
                },
                state_log_layout={"left": 16, "bottom": 16, "width": 420, "height": 132},
                link_anchor_assignments={
                    "mesh-lvl0-cloud-lvl0-hsmc": {"source": "e", "target": "w"},
                    "agg-cloud-lvl1-cloud-div-hq": {"source": "s", "target": "n"},
                },
            ),
            self.session,
        )

        fetched = get_topology_editor_state_payload(self.session)

        self.assertTrue(saved["exists"])
        self.assertEqual(fetched["layout_overrides"]["lvl0-cloud"]["x"], 320)
        self.assertEqual(fetched["state_log_layout"]["width"], 420)
        self.assertEqual(
            fetched["link_anchor_assignments"]["mesh-lvl0-cloud-lvl0-hsmc"]["target"],
            "w",
        )
        self.assertIsNotNone(fetched["updated_at"])


if __name__ == "__main__":
    unittest.main()
