"""add USB camera monitoring and recording settings

Revision ID: 0048_add_cameras
Revises: 0047_pallet_program_wcs
Create Date: 2026-08-01
"""

from alembic import op
import sqlalchemy as sa


revision = "0048_add_cameras"
down_revision = "0047_pallet_program_wcs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("app_settings") as batch_op:
        batch_op.add_column(sa.Column("camera_devices_json", sa.String(), nullable=False, server_default="[]"))
        batch_op.add_column(sa.Column("camera_idle_id", sa.String(length=100), nullable=False, server_default=""))
        batch_op.add_column(sa.Column("camera_loading_id", sa.String(length=100), nullable=False, server_default=""))
        batch_op.add_column(sa.Column("camera_machining_id", sa.String(length=100), nullable=False, server_default=""))
        batch_op.add_column(sa.Column("camera_recording_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column("camera_recording_path", sa.String(length=1000), nullable=False, server_default="data/camera-recordings"))
        batch_op.add_column(sa.Column("camera_recording_retention_days", sa.Integer(), nullable=False, server_default="7"))
        batch_op.add_column(sa.Column("camera_width", sa.Integer(), nullable=False, server_default="1920"))
        batch_op.add_column(sa.Column("camera_height", sa.Integer(), nullable=False, server_default="1080"))
        batch_op.add_column(sa.Column("camera_fps", sa.Integer(), nullable=False, server_default="30"))
        batch_op.add_column(sa.Column("camera_segment_seconds", sa.Integer(), nullable=False, server_default="300"))


def downgrade() -> None:
    with op.batch_alter_table("app_settings") as batch_op:
        for name in (
            "camera_segment_seconds",
            "camera_fps",
            "camera_height",
            "camera_width",
            "camera_recording_retention_days",
            "camera_recording_path",
            "camera_recording_enabled",
            "camera_machining_id",
            "camera_loading_id",
            "camera_idle_id",
            "camera_devices_json",
        ):
            batch_op.drop_column(name)
