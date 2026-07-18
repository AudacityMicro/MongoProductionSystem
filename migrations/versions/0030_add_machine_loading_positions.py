"""add robot and mill loading positions

Revision ID: 0030_add_machine_loading_positions
Revises: 0029_add_mill_pallet_change_g53
Create Date: 2026-07-16 18:10:00
"""

import json

from alembic import op
import sqlalchemy as sa


revision = "0030_add_machine_loading_positions"
down_revision = "0029_add_mill_pallet_change_g53"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("app_settings") as batch_op:
        batch_op.add_column(sa.Column("robot_mill_load_unload_position", sa.String(), nullable=False, server_default="{}"))
        batch_op.add_column(sa.Column("robot_mill_safe_entry_exit_position", sa.String(), nullable=False, server_default="{}"))

    connection = op.get_bind()
    rows = connection.execute(sa.text("SELECT id, mill_pallet_change_g53_position FROM app_settings")).fetchall()
    for row in rows:
        try:
            stored = json.loads(row.mill_pallet_change_g53_position or "{}")
        except (TypeError, json.JSONDecodeError):
            stored = {}
        if any(key in stored for key in ("x_in", "y_in", "z_in")):
            converted = {axis: float(stored.get(axis, 0)) for axis in ("x_in", "y_in", "z_in")}
        else:
            converted = {
                "x_in": float(stored.get("x_mm", 0)) / 25.4,
                "y_in": float(stored.get("y_mm", 0)) / 25.4,
                "z_in": float(stored.get("z_mm", 0)) / 25.4,
            }
        connection.execute(
            sa.text("UPDATE app_settings SET mill_pallet_change_g53_position = :value WHERE id = :id"),
            {"value": json.dumps(converted, separators=(",", ":")), "id": row.id},
        )


def downgrade() -> None:
    connection = op.get_bind()
    rows = connection.execute(sa.text("SELECT id, mill_pallet_change_g53_position FROM app_settings")).fetchall()
    for row in rows:
        try:
            stored = json.loads(row.mill_pallet_change_g53_position or "{}")
        except (TypeError, json.JSONDecodeError):
            stored = {}
        converted = {
            "x_mm": float(stored.get("x_in", 0)) * 25.4,
            "y_mm": float(stored.get("y_in", 0)) * 25.4,
            "z_mm": float(stored.get("z_in", 0)) * 25.4,
        }
        connection.execute(
            sa.text("UPDATE app_settings SET mill_pallet_change_g53_position = :value WHERE id = :id"),
            {"value": json.dumps(converted, separators=(",", ":")), "id": row.id},
        )
    with op.batch_alter_table("app_settings") as batch_op:
        batch_op.drop_column("robot_mill_safe_entry_exit_position")
        batch_op.drop_column("robot_mill_load_unload_position")
