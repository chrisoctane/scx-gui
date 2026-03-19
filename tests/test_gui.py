from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from scx_gui.discovery import ProgramInfo
from scx_gui.gui import ScxGuiWindow
from scx_gui.help_parser import OptionSpec
from scx_gui.runtime import CommandResult, ServiceState


class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_service_box_uses_large_clear_states(self) -> None:
        window = ScxGuiWindow(auto_refresh=False)
        try:
            window.service_state = ServiceState(
                active_state="failed",
                unit_file_state="disabled",
                sched_ext_state="disabled",
            )
            window._refresh_service_box()

            self.assertEqual(window.service_state_value_label.text(), "Failed")
            self.assertEqual(window.boot_state_value_label.text(), "Disabled")
            self.assertEqual(window.sched_ext_value_label.text(), "Disabled")
            self.assertEqual(window.service_status_label.text(), "scx.service is failed.")
            self.assertEqual(window.service_toggle_button.text(), "Start Service")
            self.assertEqual(window.boot_toggle_button.text(), "Enable At Boot")
            self.assertIn("Reset Failed State", window.reset_failed_button.text())
        finally:
            window.close()

    def test_service_toggle_buttons_flip_with_running_and_enabled_state(self) -> None:
        window = ScxGuiWindow(auto_refresh=False)
        try:
            window.service_state = ServiceState(
                active_state="active",
                unit_file_state="enabled",
                sched_ext_state="enabled",
            )
            window._refresh_service_box()

            self.assertEqual(window.service_toggle_button.text(), "Stop Service")
            self.assertEqual(window.boot_toggle_button.text(), "Disable At Boot")
        finally:
            window.close()

    def test_success_result_text_is_friendlier_when_systemctl_is_quiet(self) -> None:
        window = ScxGuiWindow(auto_refresh=False)
        try:
            result = CommandResult(
                args=["/usr/bin/pkexec", "/usr/bin/systemctl", "stop", "scx.service"],
                returncode=0,
                stdout="",
                stderr="",
            )
            text = window._format_result_text(result)

            self.assertIn("Command completed without extra output.", text)
            self.assertNotIn("(no output)", text)
        finally:
            window.close()

    def test_quick_add_dialog_uses_current_scheduler_options(self) -> None:
        window = ScxGuiWindow(auto_refresh=False)
        try:
            window.current_scheduler_name = "scx_demo"
            window.current_program = ProgramInfo(
                name="scx_demo",
                path=Path("/usr/bin/scx_demo"),
                kind="scheduler",
                summary="Demo scheduler",
                version="",
                help_text="Demo help text",
                options=[
                    OptionSpec(
                        section="Options",
                        raw_spec="--mode <MODE>",
                        short_name=None,
                        long_name="--mode",
                        metavar="<MODE>",
                        description="Pick a mode",
                        default="balanced",
                        possible_values=["balanced", "performance"],
                        repeatable=False,
                    )
                ],
                help_returncode=0,
            )
            window._open_quick_add_dialog()
            self.app.processEvents()

            self.assertIsNotNone(window.quick_add_dialog)
            self.assertIsNotNone(window.option_list)
            assert window.option_list is not None
            self.assertEqual(window.option_list.count(), 1)
            self.assertEqual(window.quick_add_dialog.windowTitle(), "scx_demo Quick Add")
        finally:
            if window.quick_add_dialog is not None:
                window.quick_add_dialog.close()
                self.app.processEvents()
            window.close()

    def test_scheduler_list_marks_proven_active_scheduler_in_green(self) -> None:
        window = ScxGuiWindow(auto_refresh=False)
        try:
            window.bundle = window.bundle.__class__(
                schedulers=[
                    ProgramInfo(
                        name="scx_demo",
                        path=Path("/usr/bin/scx_demo"),
                        kind="scheduler",
                        summary="Demo scheduler",
                        version="",
                        help_text="",
                        options=[],
                        help_returncode=0,
                    )
                ],
                utilities=[],
                docs=[],
            )
            window.current_config.scheduler = "scx_demo"
            window.service_state = ServiceState(active_state="active", active_scheduler="scx_demo")

            window._populate_scheduler_list()

            item = window.scheduler_list.item(0)
            self.assertEqual(item.text(), "scx_demo  [active]")
            self.assertEqual(item.foreground().color().name(), "#e4ffe9")
            self.assertEqual(item.background().color().name(), "#274135")
            self.assertTrue(item.font().bold())
        finally:
            window.close()

    @patch("scx_gui.gui.can_install_scx_package", return_value=True)
    def test_install_button_shows_when_scx_is_missing_after_refresh(self, _mock_can_install) -> None:
        window = ScxGuiWindow(auto_refresh=False)
        try:
            window._has_loaded_snapshot = True
            window.bundle = window.bundle.__class__(schedulers=[], utilities=[], docs=[])

            window._refresh_summary()

            assert window.install_button is not None
            self.assertFalse(window.install_button.isHidden())
            self.assertEqual(window.summary_label.text(), "SCX is not installed.")
        finally:
            window.close()

    def test_apply_scheduler_button_disables_when_selected_scheduler_is_already_live(self) -> None:
        window = ScxGuiWindow(auto_refresh=False)
        try:
            schedulers = [
                ProgramInfo(
                    name="scx_demo",
                    path=Path("/usr/bin/scx_demo"),
                    kind="scheduler",
                    summary="Demo scheduler",
                    version="",
                    help_text="",
                    options=[],
                    help_returncode=0,
                )
            ]
            window.bundle = window.bundle.__class__(schedulers=schedulers, utilities=[], docs=[])
            window.current_config.scheduler = "scx_demo"
            window.service_state = ServiceState(active_state="active", active_scheduler="scx_demo")

            window._populate_scheduler_list()

            assert window.apply_scheduler_button is not None
            self.assertFalse(window.apply_scheduler_button.isEnabled())
        finally:
            window.close()

    def test_apply_scheduler_button_enables_for_different_selected_scheduler(self) -> None:
        window = ScxGuiWindow(auto_refresh=False)
        try:
            schedulers = [
                ProgramInfo(
                    name="scx_demo",
                    path=Path("/usr/bin/scx_demo"),
                    kind="scheduler",
                    summary="Demo scheduler",
                    version="",
                    help_text="",
                    options=[],
                    help_returncode=0,
                ),
                ProgramInfo(
                    name="scx_other",
                    path=Path("/usr/bin/scx_other"),
                    kind="scheduler",
                    summary="Other scheduler",
                    version="",
                    help_text="",
                    options=[],
                    help_returncode=0,
                ),
            ]
            window.bundle = window.bundle.__class__(schedulers=schedulers, utilities=[], docs=[])
            window.current_config.scheduler = "scx_demo"
            window.service_state = ServiceState(active_state="active", active_scheduler="scx_demo")

            window._populate_scheduler_list()
            window.scheduler_list.setCurrentRow(1)
            self.app.processEvents()

            assert window.apply_scheduler_button is not None
            self.assertTrue(window.apply_scheduler_button.isEnabled())
        finally:
            window.close()


if __name__ == "__main__":
    unittest.main()
