import unittest

from app.schemas import (
    OperationalMapLinkBindingCreate,
    OperationalMapLinkCreate,
    OperationalMapObjectBindingCreate,
    OperationalMapObjectCreate,
    OperationalMapViewCreate,
)


class OperationalMapSchemaTest(unittest.TestCase):
    def test_map_view_create_validates_submap_capable_canvas(self) -> None:
        payload = OperationalMapViewCreate(
            name="2BCT Overview",
            slug="2bct-overview",
            map_type="unit",
            parent_map_id=1,
            canvas_width=2200,
            canvas_height=1400,
            default_zoom=90,
        )

        self.assertEqual(payload.map_type, "unit")
        self.assertEqual(payload.parent_map_id, 1)
        self.assertEqual(payload.canvas_width, 2200)

    def test_node_object_create_accepts_bound_node_and_ports(self) -> None:
        payload = OperationalMapObjectCreate(
            map_view_id=3,
            object_type="node",
            label="Site 4001",
            x=120,
            y=240,
            node_site_id="4001",
            binding_key="discovered:4001",
            connection_points=["north", "east", "south", "west"],
            style={"icon": "node", "shape": "rounded-rect"},
        )

        self.assertEqual(payload.object_type, "node")
        self.assertEqual(payload.node_site_id, "4001")
        self.assertEqual(payload.connection_points, ["north", "east", "south", "west"])

    def test_object_and_link_bindings_capture_status_slots(self) -> None:
        object_binding = OperationalMapObjectBindingCreate(
            object_id=8,
            slot="primary_status",
            source_type="node",
            field_name="ping",
            display_mode="badge",
        )
        link = OperationalMapLinkCreate(
            map_view_id=3,
            source_object_id=8,
            source_port="east",
            target_object_id=9,
            target_port="west",
            label="Path A",
        )
        link_binding = OperationalMapLinkBindingCreate(
            link_id=14,
            slot="label",
            source_side="target",
            field_name="latency_ms",
            display_mode="text",
        )

        self.assertEqual(object_binding.field_name, "ping")
        self.assertEqual(link.source_port, "east")
        self.assertEqual(link_binding.field_name, "latency_ms")


if __name__ == "__main__":
    unittest.main()
