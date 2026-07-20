"""Point the default mill file manager root at PathPilot's Gcode directory.

Revision ID: 0037_default_mill_files_to_gcode_directory
Revises: 0036_add_mill_results_archiving
Create Date: 2026-07-19
"""

from alembic import op


revision = "0037_default_mill_files_to_gcode_directory"
down_revision = "0036_add_mill_results_archiving"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Preserve intentionally customized controller directories while correcting
    # installations that still use the old application default.
    op.execute(
        """
        UPDATE app_settings
        SET mill_file_directory = '/home/operator/gcode/Gcode',
            mill_results_source_path = CASE
                WHEN mill_results_source_path = '/home/operator/gcode/RESULTS.TXT'
                THEN '/home/operator/gcode/Gcode/RESULTS.TXT'
                ELSE mill_results_source_path
            END,
            mill_results_archive_directory = CASE
                WHEN mill_results_archive_directory = '/home/operator/gcode/Results'
                THEN '/home/operator/gcode/Gcode/Results'
                ELSE mill_results_archive_directory
            END
        WHERE mill_file_directory = '/home/operator/gcode'
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE app_settings
        SET mill_file_directory = '/home/operator/gcode',
            mill_results_source_path = CASE
                WHEN mill_results_source_path = '/home/operator/gcode/Gcode/RESULTS.TXT'
                THEN '/home/operator/gcode/RESULTS.TXT'
                ELSE mill_results_source_path
            END,
            mill_results_archive_directory = CASE
                WHEN mill_results_archive_directory = '/home/operator/gcode/Gcode/Results'
                THEN '/home/operator/gcode/Results'
                ELSE mill_results_archive_directory
            END
        WHERE mill_file_directory = '/home/operator/gcode/Gcode'
        """
    )
