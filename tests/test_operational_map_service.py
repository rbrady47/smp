import unittest

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db import Base
from app.models import DiscoveredNode, Node
from app.operational_map_service import (
    create_map_link,
    create_map_link_binding,
    create_map_object,
    create_map_object_binding,
    create_map_view,
    get_map_view_detail,
    list_map_views,
    update_map_object,
)
from app.schemas import (
    OperationalMapLinkBindingCreate,
    OperationalMapLinkCreate,
    OperationalMapObjectBindingCreate,
    OperationalMapObjectCreate,
    OperationalMapObjectUpdate,
    OperationalMapViewCreate,
)


class OperationalMapServiceTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.async_session_factory = async_sessionmaker(
            bind=self.engine, class_=AsyncSession, expire_on_commit=False,
        )
        self.session = self.async_session_factory()
        self.session.add(
            Node(
                id=1,
                name="Anchor A",
                node_id="1001",
                host="10.0.0.1",
                web_port=443,
                ssh_port=22,
                location="Cloud",
                topology_map_id=0,
                enabled=True,
                notes=None,
                api_username=None,
                api_password=None,
                api_use_https=False,
            )
        )
        self.session.add(
            DiscoveredNode(
                site_id="4001",
                site_name="Delta",
                host="10.10.10.10",
                location="Cloud",
                unit="DIV HQ",
                version="1.2.3",
                discovered_level=2,
            )
        )
        await self.session.commit()

    async def asyncTearDown(self) -> None:
        await self.session.close()
        await self.engine.dispose()

    async def test_map_view_crud_starter_lists_created_views(self) -> None:
        created = await create_map_view(
            OperationalMapViewCreate(
                name="Global Map",
                slug="global-map",
                map_type="global",
            ),
            self.session,
        )

        views = await list_map_views(self.session)

        self.assertEqual(created["slug"], "global-map")
        self.assertEqual(len(views), 1)
        self.assertEqual(views[0]["name"], "Global Map")

    async def test_map_detail_returns_objects_links_and_bindings(self) -> None:
        parent_map = await create_map_view(
            OperationalMapViewCreate(
                name="Global Map",
                slug="global-map",
                map_type="global",
            ),
            self.session,
        )
        child_map = await create_map_view(
            OperationalMapViewCreate(
                name="DIV HQ Submap",
                slug="div-hq-submap",
                map_type="unit",
                parent_map_id=parent_map["id"],
            ),
            self.session,
        )

        node_object = await create_map_object(
            OperationalMapObjectCreate(
                map_view_id=parent_map["id"],
                object_type="node",
                label="Anchor A",
                node_site_id="1001",
                connection_points=["north", "east", "south", "west"],
            ),
            self.session,
        )
        submap_object = await create_map_object(
            OperationalMapObjectCreate(
                map_view_id=parent_map["id"],
                object_type="submap",
                label="DIV HQ",
                child_map_view_id=child_map["id"],
                connection_points=["west", "east"],
            ),
            self.session,
        )
        object_binding = await create_map_object_binding(
            OperationalMapObjectBindingCreate(
                object_id=node_object["id"],
                slot="primary_status",
                source_type="node",
                field_name="ping",
                display_mode="badge",
            ),
            self.session,
        )
        link = await create_map_link(
            OperationalMapLinkCreate(
                map_view_id=parent_map["id"],
                source_object_id=node_object["id"],
                source_port="east",
                target_object_id=submap_object["id"],
                target_port="west",
                label="RTT",
            ),
            self.session,
        )
        link_binding = await create_map_link_binding(
            OperationalMapLinkBindingCreate(
                link_id=link["id"],
                slot="label",
                source_side="source",
                field_name="latency_ms",
                display_mode="text",
            ),
            self.session,
        )

        detail = await get_map_view_detail(parent_map["id"], self.session)

        self.assertEqual(detail["map_view"]["slug"], "global-map")
        self.assertEqual(len(detail["objects"]), 2)
        self.assertEqual(detail["objects"][0]["binding_key"], "anchor:1")
        self.assertEqual(detail["object_bindings"][0]["id"], object_binding["id"])
        self.assertEqual(detail["links"][0]["id"], link["id"])
        self.assertEqual(detail["link_bindings"][0]["id"], link_binding["id"])
        self.assertEqual(detail["available_nodes"][0]["binding_key"], "anchor:1")
        self.assertEqual(detail["available_nodes"][1]["binding_key"], "discovered:4001")
        self.assertEqual(detail["available_submaps"][0]["id"], child_map["id"])
        self.assertIn("primary_status", detail["object_binding_catalog"])
        self.assertIn("label", detail["link_binding_catalog"])

    async def test_node_object_can_be_reassigned_to_discovered_node_id(self) -> None:
        map_view = await create_map_view(
            OperationalMapViewCreate(
                name="Global Map",
                slug="global-map",
                map_type="global",
            ),
            self.session,
        )
        node_object = await create_map_object(
            OperationalMapObjectCreate(
                map_view_id=map_view["id"],
                object_type="node",
                label="Placeholder",
                node_site_id="1001",
                connection_points=["north"],
            ),
            self.session,
        )

        updated = await update_map_object(
            node_object["id"],
            OperationalMapObjectUpdate(node_site_id="4001"),
            self.session,
        )

        self.assertEqual(updated["node_site_id"], "4001")
        self.assertEqual(updated["binding_key"], "discovered:4001")


if __name__ == "__main__":
    unittest.main()
