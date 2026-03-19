from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scx_gui.runtime import (
    ScxConfig,
    _parse_systemd_environment,
    install_scx_package,
    read_scx_config,
    render_scx_config,
)


class RuntimeTests(unittest.TestCase):
    def test_render_config_preserves_comments_and_adds_flags(self) -> None:
        original_lines = [
            "# comment",
            "SCX_SCHEDULER=scx_flash",
            "",
            "# example flags",
        ]
        rendered = render_scx_config(
            ScxConfig(
                scheduler="scx_lavd",
                flags_raw="--performance --slice-max-us 4000",
                original_lines=original_lines,
            )
        )

        self.assertIn("SCX_SCHEDULER=scx_lavd", rendered)
        self.assertIn("SCX_FLAGS='--performance --slice-max-us 4000'", rendered)
        self.assertIn("# comment", rendered)

    def test_read_config_parses_shell_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "scx"
            path.write_text(
                "SCX_SCHEDULER=scx_flash\nSCX_FLAGS='--mode performance --slice-us 1000'\n",
                encoding="utf-8",
            )
            config = read_scx_config(path)

        self.assertEqual(config.scheduler, "scx_flash")
        self.assertEqual(config.flags_raw, "--mode performance --slice-us 1000")

    def test_parse_systemd_environment_extracts_overrides(self) -> None:
        environment = 'SCX_SCHEDULER_OVERRIDE=scx_lavd SCX_FLAGS_OVERRIDE="--performance --slice-max-us 4000"'
        parsed = _parse_systemd_environment(environment)

        self.assertEqual(parsed["SCX_SCHEDULER_OVERRIDE"], "scx_lavd")
        self.assertEqual(parsed["SCX_FLAGS_OVERRIDE"], "--performance --slice-max-us 4000")

    @patch("scx_gui.runtime.can_install_scx_package", return_value=False)
    def test_install_scx_package_reports_missing_zypper(self, _mock_can_install) -> None:
        result = install_scx_package()

        self.assertEqual(result.returncode, 127)
        self.assertIn("zypper", result.stderr)

    @patch("scx_gui.runtime.run_command")
    @patch("scx_gui.runtime.can_install_scx_package", return_value=True)
    def test_install_scx_package_uses_zypper(self, _mock_can_install, mock_run_command) -> None:
        install_scx_package()

        mock_run_command.assert_called_once_with(
            ["/usr/bin/zypper", "--non-interactive", "install", "--auto-agree-with-licenses", "scx"],
            require_root=True,
            timeout=1200,
        )


if __name__ == "__main__":
    unittest.main()
