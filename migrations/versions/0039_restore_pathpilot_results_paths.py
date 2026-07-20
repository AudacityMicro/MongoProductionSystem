"""restore PathPilot RESULTS.TXT locations beside the Gcode directory

Revision ID: 0039_restore_pathpilot_results_paths
Revises: 0038_add_run_mode_alert
Create Date: 2026-07-19
"""

from alembic import op


revision = "0039_restore_pathpilot_results_paths"
down_revision = "0038_add_run_mode_alert"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # PathPilot writes RESULTS.TXT and creates its results directory beside
    # Gcode. Migration 0037 incorrectly relocated these defaults into Gcode.
    op.execute(
        """
        UPDATE app_settings
        SET mill_results_source_path = '/home/operator/gcode/RESULTS.TXT',
            mill_results_archive_directory = '/home/operator/gcode/results'
        WHERE mill_file_directory = '/home/operator/gcode/Gcode'
          AND mill_results_source_path = '/home/operator/gcode/Gcode/RESULTS.TXT'
          AND mill_results_archive_directory = '/home/operator/gcode/Gcode/Results'
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE app_settings
        SET mill_results_source_path = '/home/operator/gcode/Gcode/RESULTS.TXT',
            mill_results_archive_directory = '/home/operator/gcode/Gcode/Results'
        WHERE mill_file_directory = '/home/operator/gcode/Gcode'
          AND mill_results_source_path = '/home/operator/gcode/RESULTS.TXT'
          AND mill_results_archive_directory = '/home/operator/gcode/results'
        """
    )
