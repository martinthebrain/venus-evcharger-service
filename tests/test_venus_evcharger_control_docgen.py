# SPDX-License-Identifier: GPL-3.0-or-later
import pathlib
import unittest

from venus_evcharger.control import (
    GENERATED_MARKDOWN_BLOCK_RENDERERS,
    replace_generated_markdown_block,
)
from venus_evcharger.control.docgen import (
    render_api_overview_client_starting_points_markdown,
    render_control_api_getting_started_markdown,
    render_readme_local_http_control_api_getting_started_markdown,
)


def _repo_root() -> pathlib.Path:
    root = pathlib.Path(__file__).resolve().parents[1]
    return root.parent if root.name == "mutants" else root


class TestVenusEvchargerControlDocgen(unittest.TestCase):
    def test_generated_markdown_blocks_match_documents(self) -> None:
        document_blocks = {
            "CONTROL_API.md": ("CONTROL_API_COMMAND_MATRIX", "CONTROL_API_GETTING_STARTED"),
            "API_OVERVIEW.md": ("API_OVERVIEW_CLIENT_STARTING_POINTS",),
            "README.md": ("README_LOCAL_HTTP_CONTROL_API_GETTING_STARTED",),
        }

        for relative_path, block_names in document_blocks.items():
            with self.subTest(path=relative_path):
                document = (_repo_root() / relative_path).read_text(encoding="utf-8")
                for block_name in block_names:
                    begin_marker = f"<!-- BEGIN:{block_name} -->"
                    end_marker = f"<!-- END:{block_name} -->"
                    begin = document.index(begin_marker) + len(begin_marker)
                    end = document.index(end_marker)
                    self.assertEqual(
                        document[begin:end].strip(),
                        GENERATED_MARKDOWN_BLOCK_RENDERERS[block_name]().strip(),
                    )

    def test_replace_generated_markdown_block_is_no_op_for_rendered_content(self) -> None:
        original = "<!-- BEGIN:BLOCK -->\nold\n<!-- END:BLOCK -->"
        updated = replace_generated_markdown_block(original, "BLOCK", "new")
        self.assertEqual(updated, "<!-- BEGIN:BLOCK -->\nnew\n<!-- END:BLOCK -->")

    def test_replace_generated_markdown_block_preserves_surrounding_document(self) -> None:
        original = "before\n<!-- BEGIN:BLOCK -->\nold\n<!-- END:BLOCK -->\nafter\n"
        updated = replace_generated_markdown_block(original, "BLOCK", "new\n\n")
        self.assertEqual(updated, "before\n<!-- BEGIN:BLOCK -->\nnew\n<!-- END:BLOCK -->\nafter\n")

        repeated = (
            "one\n<!-- BEGIN:BLOCK -->\nold-1\n<!-- END:BLOCK -->\n"
            "two\n<!-- BEGIN:BLOCK -->\nold-2\n<!-- END:BLOCK -->\n"
        )
        updated_repeated = replace_generated_markdown_block(repeated, "BLOCK", "new")
        self.assertEqual(
            updated_repeated,
            "one\n<!-- BEGIN:BLOCK -->\nnew\n<!-- END:BLOCK -->\n"
            "two\n<!-- BEGIN:BLOCK -->\nold-2\n<!-- END:BLOCK -->\n",
        )

        with self.assertRaises(ValueError):
            replace_generated_markdown_block("<!-- BEGIN:OTHER -->\nold\n<!-- END:OTHER -->", "BLOCK", "new")

    def test_renderer_registry_and_key_fragments_are_stable(self) -> None:
        self.assertEqual(
            set(GENERATED_MARKDOWN_BLOCK_RENDERERS),
            {
                "CONTROL_API_COMMAND_MATRIX",
                "CONTROL_API_GETTING_STARTED",
                "API_OVERVIEW_CLIENT_STARTING_POINTS",
                "README_LOCAL_HTTP_CONTROL_API_GETTING_STARTED",
            },
        )
        self.assertIs(
            GENERATED_MARKDOWN_BLOCK_RENDERERS["CONTROL_API_GETTING_STARTED"],
            render_control_api_getting_started_markdown,
        )
        self.assertIs(
            GENERATED_MARKDOWN_BLOCK_RENDERERS["API_OVERVIEW_CLIENT_STARTING_POINTS"],
            render_api_overview_client_starting_points_markdown,
        )
        self.assertIs(
            GENERATED_MARKDOWN_BLOCK_RENDERERS["README_LOCAL_HTTP_CONTROL_API_GETTING_STARTED"],
            render_readme_local_http_control_api_getting_started_markdown,
        )

        getting_started = render_control_api_getting_started_markdown()
        self.assertNotIn("/home/", getting_started)
        for expected in (
            "Official example files:",
            "[examples/control_api_client.py](examples/control_api_client.py)",
            "CLI quick start:",
            "python3 ./venus_evchargerctl.py --token READ-TOKEN health",
            "python3 ./venus_evchargerctl.py --token CONTROL-TOKEN safe-write set-mode 1",
            "curl --unix-socket /run/venus-evcharger-control.sock \\",
            "Use `If-Match` with the current state token:",
            "from venus_evcharger.control.client import LocalControlApiClient",
        ):
            self.assertIn(expected, getting_started)

        overview = render_api_overview_client_starting_points_markdown()
        self.assertNotIn("/home/", overview)
        self.assertIn("Practical local client entrypoints in this repository:", overview)
        self.assertIn("[CONTROL_API.md](CONTROL_API.md) and [STATE_API.md](STATE_API.md).", overview)

        readme = render_readme_local_http_control_api_getting_started_markdown()
        self.assertIn("Quick start:", readme)
        self.assertIn("For direct HTTP usage, `curl` snippets, optimistic concurrency with `If-Match`,", readme)


if __name__ == "__main__":
    unittest.main()
