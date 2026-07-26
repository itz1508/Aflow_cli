"""Stage 4: Repository evidence collection."""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RepositoryEvidence:
    """Collected repository evidence for gap analysis."""
    repository_root: str
    structure: list[str]
    source_files: list[str]
    config_files: list[str]
    dependency_declarations: list[str]
    schemas_and_interfaces: list[str]
    existing_tests: list[str]
    validation_commands: dict[str, str]
    documented_constraints: list[str]
    evidence_files: dict[str, str] = field(default_factory=dict)

    def to_mapping(self) -> dict[str, Any]:
        """Convert to JSON-serializable dict."""
        return {
            "repository_root": self.repository_root,
            "structure": self.structure,
            "source_files": self.source_files,
            "config_files": self.config_files,
            "dependency_declarations": self.dependency_declarations,
            "schemas_and_interfaces": self.schemas_and_interfaces,
            "existing_tests": self.existing_tests,
            "validation_commands": self.validation_commands,
            "documented_constraints": self.documented_constraints,
            "evidence_files": self.evidence_files,
        }


def _collect_files(root: Path, patterns: list[str]) -> list[str]:
    """Collect files matching glob patterns under root."""
    files = []
    for pattern in patterns:
        for p in root.glob(pattern):
            files.append(str(p.relative_to(root)))
    return sorted(set(files))


def _read_file_safe(root: Path, rel_path: str) -> str | None:
    """Read file content if exists, return None otherwise."""
    full_path = root / rel_path
    if full_path.exists() and full_path.is_file():
        return full_path.read_text(encoding="utf-8", errors="replace")
    return None


def _compute_file_hash(root: Path, rel_path: str) -> str:
    """Compute SHA-256 hash of file content."""
    content = _read_file_safe(root, rel_path)
    if content is None:
        return ""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def collect_repository_evidence(*, repository_root: str) -> RepositoryEvidence:
    """Collect repository evidence before gap discovery.

    Collects:
    - repository structure
    - relevant source files
    - configuration files
    - dependency declarations
    - schemas and interfaces
    - existing tests
    - supported validation commands
    - documented constraints
    """
    root = Path(repository_root)
    if not root.exists():
        raise ValueError(f"Repository root does not exist: {repository_root}")

    # Collect structure (all .py, .toml, .json files at root level)
    structure = [p.name for p in root.iterdir() if p.is_file()]

    # Source files
    source_files = _collect_files(root, ["**/*.py", "**/*.toml"])

    # Config files
    config_files = _collect_files(root, ["*.toml", "*.json", ".gitignore", "*.md"])

    # Dependency declarations
    dep_files = _collect_files(root, ["pyproject.toml", "package.json", "requirements.txt"])

    # Schemas and interfaces
    schema_files = _collect_files(root, ["**/schemas/**/*.json", "**/schemas/**/*.py"])

    # Existing tests
    test_files = _collect_files(root, ["**/test_*.py", "**/tests/**/*.py"])

    # Validation commands (detected from standard config files)
    validation_commands = {}
    pyproject = _read_file_safe(root, "pyproject.toml")
    if pyproject and "pytest" in pyproject.lower():
        validation_commands["test"] = "uv run pytest -q"
    validation_commands["lint"] = "ruff check ."

    # Documented constraints
    constraints = []
    readme = _read_file_safe(root, "README.md")
    if readme:
        constraints.append("README exists")

    # Evidence file hashes
    evidence_files = {}
    for sf in source_files[:20]:  # Limit to 20 files for performance
        h = _compute_file_hash(root, sf)
        if h:
            evidence_files[sf] = h

    return RepositoryEvidence(
        repository_root=repository_root,
        structure=structure,
        source_files=source_files,
        config_files=config_files,
        dependency_declarations=dep_files,
        schemas_and_interfaces=schema_files,
        existing_tests=test_files,
        validation_commands=validation_commands,
        documented_constraints=constraints,
        evidence_files=evidence_files,
    )