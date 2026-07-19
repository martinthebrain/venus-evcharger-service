#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Check Python syntax without py_compile or compileall.

Venus OS images may omit those standard-library modules.  The built-in
``compile`` function provides the same syntax validation without importing
project code or writing bytecode files.
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Sequence


def python_files(paths: Sequence[str]) -> list[str]:
    """Return unique Python files below the supplied files and directories."""
    candidates: list[str] = []
    for supplied_path in paths:
        candidates.extend(_path_python_files(supplied_path))
    normalized = (os.path.normpath(candidate) for candidate in candidates)
    return list(dict.fromkeys(normalized))


def _path_python_files(supplied_path: str) -> list[str]:
    path = os.path.normpath(supplied_path)
    if os.path.isfile(path):
        return [path] if path.endswith(".py") else []
    if os.path.isdir(path):
        return _directory_python_files(path)
    raise ValueError(f"syntax-check path does not exist: {supplied_path}")


def _directory_python_files(path: str) -> list[str]:
    result: list[str] = []
    for root, directories, filenames in os.walk(path):
        directories[:] = sorted(set(directories).difference({"__pycache__"}))
        python_names = sorted(filter(_is_python_filename, filenames))
        result.extend(os.path.join(root, name) for name in python_names)
    return result


def _is_python_filename(name: str) -> bool:
    return name.endswith(".py")


def check_python_syntax(paths: Sequence[str]) -> int:
    files = python_files(paths)
    if not files:
        raise ValueError("syntax check did not select any Python files")
    failures = 0
    for path in files:
        error = _syntax_error(path)
        if error is None:
            continue
        failures += 1
        print(f"{path}: {error}")
    if failures:
        print(f"Python syntax failed: {failures} of {len(files)} files")
        return 1
    print(f"Python syntax OK: {len(files)} files")
    return 0


def _syntax_error(path: str) -> Exception | None:
    try:
        with open(path, "rb") as source_file:
            compile(source_file.read(), path, "exec", dont_inherit=True)
    except (OSError, SyntaxError, ValueError) as error:
        return error
    return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", help="Python files or directories to check")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        return check_python_syntax(args.paths)
    except ValueError as error:
        print(error)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
