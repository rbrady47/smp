import unittest

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db import Base
from app.schemas import TopologyEditorStateUpdate
from app.topology_editor_state_service import (
    get_topology_editor_state_payload,
    upsert_topology_editor_state,
)


class TopologyEditorStateServiceTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.async_session_factory = async_sessionmaker(
            bind=self.engine, class_=AsyncSession, expire_on_commit=False,
        )
        self.session = self.async_session_factory()

    async def asyncTearDown(self) -> None:
        await self.session.close()
        await self.engine.dispose()

    async def test_get_returns_empty_payload_when_state_not_created(self) -> None:
        payload = await get_topology_editor_state_payload(self.session)

        self.assertEqual(payload["scope"], "default")
        self.assertFalse(payload["exists"])
        self.assertEqual(payload["layout_overrides"], {})
        self.assertIsNone(payload["state_log_layout"])
        self.assertEqual(payload["link_anchor_assignments"], {})
        self.assertEqual(payload["demo_mode"], "off")

    async def test_upsert_round_trips_layout_log_link_assignments_and_demo_mode(self) -> None:
        saved = await upsert_topology_editor_state(
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
                demo_mode="mix",
            ),
            self.session,
        )

        fetched = await get_topology_editor_state_payload(self.session)

        self.assertTrue(saved["exists"])
        self.assertEqual(fetched["layout_overrides"]["lvl0-cloud"]["x"], 320)
        self.assertEqual(fetched["state_log_layout"]["width"], 420)
        self.assertEqual(
            fetched["link_anchor_assignments"]["mesh-lvl0-cloud-lvl0-hsmc"]["target"],
            "w",
        )
        self.assertEqual(fetched["demo_mode"], "mix")
        self.assertIsNotNone(fetched["updated_at"])


if __name__ == "__main__":
    unittest.main()
