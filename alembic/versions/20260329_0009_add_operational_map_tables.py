"""add operational map tables

Revision ID: 20260329_0009
Revises: 20260327_0008
Create Date: 2026-03-29 09:30:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "20260329_0009"
down_revision = "20260327_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "operational_map_views",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("slug", sa.String(length=120), nullable=False),
        sa.Column("map_type", sa.String(length=32), nullable=False),
        sa.Column("parent_map_id", sa.Integer(), nullable=True),
        sa.Column("background_image_url", sa.String(length=512), nullable=True),
        sa.Column("canvas_width", sa.Integer(), nullable=False),
        sa.Column("canvas_height", sa.Integer(), nullable=False),
        sa.Column("default_zoom", sa.Integer(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["parent_map_id"], ["operational_map_views.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_operational_map_views_id"), "operational_map_views", ["id"], unique=False)
    op.create_index(op.f("ix_operational_map_views_parent_map_id"), "operational_map_views", ["parent_map_id"], unique=False)
    op.create_index(op.f("ix_operational_map_views_slug"), "operational_map_views", ["slug"], unique=True)

    op.create_table(
        "operational_map_objects",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("map_view_id", sa.Integer(), nullable=False),
        sa.Column("object_type", sa.String(length=32), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=True),
        sa.Column("x", sa.Integer(), nullable=False),
        sa.Column("y", sa.Integer(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("z_index", sa.Integer(), nullable=False),
        sa.Column("node_site_id", sa.String(length=64), nullable=True),
        sa.Column("binding_key", sa.String(length=128), nullable=True),
        sa.Column("child_map_view_id", sa.Integer(), nullable=True),
        sa.Column("connection_points_json", sa.Text(), nullable=True),
        sa.Column("style_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["child_map_view_id"], ["operational_map_views.id"]),
        sa.ForeignKeyConstraint(["map_view_id"], ["operational_map_views.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_operational_map_objects_binding_key"), "operational_map_objects", ["binding_key"], unique=False)
    op.create_index(op.f("ix_operational_map_objects_child_map_view_id"), "operational_map_objects", ["child_map_view_id"], unique=False)
    op.create_index(op.f("ix_operational_map_objects_id"), "operational_map_objects", ["id"], unique=False)
    op.create_index(op.f("ix_operational_map_objects_map_view_id"), "operational_map_objects", ["map_view_id"], unique=False)
    op.create_index(op.f("ix_operational_map_objects_node_site_id"), "operational_map_objects", ["node_site_id"], unique=False)

    op.create_table(
        "operational_map_links",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("map_view_id", sa.Integer(), nullable=False),
        sa.Column("source_object_id", sa.Integer(), nullable=False),
        sa.Column("source_port", sa.String(length=64), nullable=False),
        sa.Column("target_object_id", sa.Integer(), nullable=False),
        sa.Column("target_port", sa.String(length=64), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=True),
        sa.Column("style_json", sa.Text(), nullable=True),
        sa.Column("points_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["map_view_id"], ["operational_map_views.id"]),
        sa.ForeignKeyConstraint(["source_object_id"], ["operational_map_objects.id"]),
        sa.ForeignKeyConstraint(["target_object_id"], ["operational_map_objects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_operational_map_links_id"), "operational_map_links", ["id"], unique=False)
    op.create_index(op.f("ix_operational_map_links_map_view_id"), "operational_map_links", ["map_view_id"], unique=False)
    op.create_index(op.f("ix_operational_map_links_source_object_id"), "operational_map_links", ["source_object_id"], unique=False)
    op.create_index(op.f("ix_operational_map_links_target_object_id"), "operational_map_links", ["target_object_id"], unique=False)

    op.create_table(
        "operational_map_object_bindings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("object_id", sa.Integer(), nullable=False),
        sa.Column("slot", sa.String(length=64), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("field_name", sa.String(length=64), nullable=False),
        sa.Column("display_mode", sa.String(length=32), nullable=True),
        sa.Column("settings_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["object_id"], ["operational_map_objects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_operational_map_object_bindings_id"), "operational_map_object_bindings", ["id"], unique=False)
    op.create_index(op.f("ix_operational_map_object_bindings_object_id"), "operational_map_object_bindings", ["object_id"], unique=False)

    op.create_table(
        "operational_map_link_bindings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("link_id", sa.Integer(), nullable=False),
        sa.Column("slot", sa.String(length=64), nullable=False),
        sa.Column("source_side", sa.String(length=32), nullable=False),
        sa.Column("field_name", sa.String(length=64), nullable=False),
        sa.Column("display_mode", sa.String(length=32), nullable=True),
        sa.Column("settings_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["link_id"], ["operational_map_links.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_operational_map_link_bindings_id"), "operational_map_link_bindings", ["id"], unique=False)
    op.create_index(op.f("ix_operational_map_link_bindings_link_id"), "operational_map_link_bindings", ["link_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_operational_map_link_bindings_link_id"), table_name="operational_map_link_bindings")
    op.drop_index(op.f("ix_operational_map_link_bindings_id"), table_name="operational_map_link_bindings")
    op.drop_table("operational_map_link_bindings")

    op.drop_index(op.f("ix_operational_map_object_bindings_object_id"), table_name="operational_map_object_bindings")
    op.drop_index(op.f("ix_operational_map_object_bindings_id"), table_name="operational_map_object_bindings")
    op.drop_table("operational_map_object_bindings")

    op.drop_index(op.f("ix_operational_map_links_target_object_id"), table_name="operational_map_links")
    op.drop_index(op.f("ix_operational_map_links_source_object_id"), table_name="operational_map_links")
    op.drop_index(op.f("ix_operational_map_links_map_view_id"), table_name="operational_map_links")
    op.drop_index(op.f("ix_operational_map_links_id"), table_name="operational_map_links")
    op.drop_table("operational_map_links")

    op.drop_index(op.f("ix_operational_map_objects_node_site_id"), table_name="operational_map_objects")
    op.drop_index(op.f("ix_operational_map_objects_map_view_id"), table_name="operational_map_objects")
    op.drop_index(op.f("ix_operational_map_objects_id"), table_name="operational_map_objects")
    op.drop_index(op.f("ix_operational_map_objects_child_map_view_id"), table_name="operational_map_objects")
    op.drop_index(op.f("ix_operational_map_objects_binding_key"), table_name="operational_map_objects")
    op.drop_table("operational_map_objects")

    op.drop_index(op.f("ix_operational_map_views_slug"), table_name="operational_map_views")
    op.drop_index(op.f("ix_operational_map_views_parent_map_id"), table_name="operational_map_views")
    op.drop_index(op.f("ix_operational_map_views_id"), table_name="operational_map_views")
    op.drop_table("operational_map_views")
