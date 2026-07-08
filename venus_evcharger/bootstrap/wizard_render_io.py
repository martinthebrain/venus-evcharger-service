# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from venus_evcharger.backend.probe import validate_wallbox_config
from venus_evcharger.bootstrap.wizard_render_secrets import redact_sensitive_rendered_setup
from venus_evcharger.bootstrap.wizard_render_text import timestamp


def materialized_config_text(config_text: str, output_dir: Path, adapter_files: dict[str, str]) -> str:
    rendered = config_text
    for relative_path in sorted(adapter_files):
        rendered = rendered.replace(f"={relative_path}\n", f"={output_dir / relative_path}\n")
    return rendered


def write_private_text(target: Path, content: str) -> None:
    """Write sensitive wizard material with owner-only permissions."""
    target.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    with os.fdopen(os.open(target, flags, 0o600), "w", encoding="utf-8") as handle:
        handle.write(content)
    target.chmod(0o600)


def materialize_rendered_setup(
    config_text: str,
    output_dir: Path,
    adapter_files: dict[str, str],
    config_name: str,
) -> Path:
    """Write one rendered setup to disk using private file permissions."""
    materialized_text = materialized_config_text(config_text, output_dir, adapter_files)
    main_path = output_dir / config_name
    write_private_text(main_path, materialized_text)
    for relative_path, content in adapter_files.items():
        write_private_text(output_dir / relative_path, content)
    return main_path


def validate_rendered_setup(config_text: str, adapter_files: dict[str, str], config_name: str) -> dict[str, object]:
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        redacted_config_text, redacted_adapter_files = redact_sensitive_rendered_setup(config_text, adapter_files)
        main_path = materialize_rendered_setup(redacted_config_text, temp_path, redacted_adapter_files, config_name)
        return validate_wallbox_config(str(main_path))


def backup_path(target: Path) -> Path:
    return target.with_name(f"{target.name}.wizard-backup-{timestamp()}")


def write_with_backup(target: Path, content: str) -> str | None:
    backup_file: str | None = None
    if target.exists():
        destination = backup_path(target)
        shutil.copy2(target, destination)
        backup_file = str(destination)
    write_private_text(target, content)
    return backup_file


def write_generated_files(config_path: Path, materialized_text: str, adapter_files: dict[str, str]) -> list[str]:
    backup_files: list[str] = []
    backup_file = write_with_backup(config_path, materialized_text)
    if backup_file is not None:
        backup_files.append(backup_file)
    for relative_path, content in sorted(adapter_files.items()):
        backup_file = write_with_backup(config_path.parent / relative_path, content)
        if backup_file is not None:
            backup_files.append(backup_file)
    return backup_files
