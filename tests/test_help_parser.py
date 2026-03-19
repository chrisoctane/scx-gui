from __future__ import annotations

import unittest

from scx_gui.help_parser import parse_help_text


SAMPLE_HELP = """
Example scheduler summary.

Usage: scx_example [OPTIONS]

Options:
  -s, --slice-us <SLICE_US>
          Maximum slice duration [default: 1000]
      --mode <MODE>
          Scheduling mode [default: balanced] [possible values: balanced, performance]
  -v, --verbose...
          Increase verbosity
      --cpumasks <CPUMASKS>...
          Repeated mask value
  -h, --help
          Print help
"""

EQUALS_HELP = """
Another scheduler.

Usage: scx_equals [OPTIONS]

Options:
      --mode=<MODE>
          Pick a mode [possible values: balanced, performance]
This is a very long descriptive sentence:
  -s, --slice-us <SLICE_US>
          Scheduling slice [default: 1000]
"""


class HelpParserTests(unittest.TestCase):
    def test_parse_help_extracts_summary_and_options(self) -> None:
        parsed = parse_help_text(SAMPLE_HELP)

        self.assertEqual(parsed.summary, "Example scheduler summary.")
        self.assertEqual(len(parsed.options), 5)

        slice_option = next(option for option in parsed.options if option.long_name == "--slice-us")
        self.assertEqual(slice_option.short_name, "-s")
        self.assertEqual(slice_option.metavar, "<SLICE_US>")
        self.assertEqual(slice_option.default, "1000")
        self.assertFalse(slice_option.repeatable)

        mode_option = next(option for option in parsed.options if option.long_name == "--mode")
        self.assertEqual(mode_option.possible_values, ["balanced", "performance"])

        verbose_option = next(option for option in parsed.options if option.long_name == "--verbose")
        self.assertTrue(verbose_option.repeatable)
        self.assertTrue(verbose_option.is_boolean_flag)

        cpumasks_option = next(option for option in parsed.options if option.long_name == "--cpumasks")
        self.assertTrue(cpumasks_option.repeatable)
        self.assertEqual(cpumasks_option.metavar, "<CPUMASKS>")

    def test_parse_help_handles_equals_value_syntax(self) -> None:
        parsed = parse_help_text(EQUALS_HELP)

        mode_option = next(option for option in parsed.options if option.long_name == "--mode")
        self.assertEqual(mode_option.metavar, "<MODE>")
        self.assertEqual(mode_option.possible_values, ["balanced", "performance"])

    def test_long_body_lines_do_not_become_section_headers(self) -> None:
        parsed = parse_help_text(EQUALS_HELP)

        slice_option = next(option for option in parsed.options if option.long_name == "--slice-us")
        self.assertEqual(slice_option.section, "Options")


if __name__ == "__main__":
    unittest.main()
