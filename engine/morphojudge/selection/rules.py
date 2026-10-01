"""Selection rules (SEL-001).

Rules are data, not code: the same SelectionRules value must reproduce the
same decisions for the same input (FR-010). Rule ids are frozen strings;
new rules may only append new ids.
"""

from __future__ import annotations

from dataclasses import dataclass, field

RULE_SELECTED = "SEL-SELECTED"
RULE_UNSUPPORTED_LANGUAGE = "SEL-UNSUPPORTED-LANGUAGE"
RULE_BINARY = "SEL-BINARY"
RULE_SYMLINK = "SEL-SYMLINK"
RULE_SUBMODULE = "SEL-SUBMODULE"
RULE_PRIVATE_PATH = "SEL-PRIVATE-PATH"
RULE_CREDENTIAL_FILE = "SEL-CREDENTIAL-FILE"
RULE_PATH_TRAVERSAL = "SEL-PATH-TRAVERSAL"
RULE_OVERSIZE = "SEL-OVERSIZE"
RULE_BUDGET_EXHAUSTED = "SEL-BUDGET-EXHAUSTED"
RULE_SIZE_UNKNOWN = "SEL-SIZE-UNKNOWN"
RULE_UNMERGED = "SEL-UNMERGED"
RULE_NOT_WHITELISTED = "SEL-DEP-NOT-WHITELISTED"
RULE_DEP_BUDGET_EXHAUSTED = "SEL-DEP-BUDGET-EXHAUSTED"

LANGUAGE_BY_EXTENSION: dict[str, str] = {
    ".ts": "typescript",
    ".tsx": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".py": "python",
    ".md": "markdown",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".css": "css",
    ".html": "html",
    ".sh": "shell",
    ".bash": "shell",
    ".sql": "sql",
    ".txt": "text",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".gif": "image",
    ".ico": "image",
    ".svg": "svg",
    ".pdf": "pdf",
    ".woff": "font",
    ".woff2": "font",
}

SUPPORTED_LANGUAGES = frozenset({"typescript", "javascript", "python"})

CREDENTIAL_BASENAMES = frozenset(
    {
        ".env",
        ".env.local",
        ".env.development",
        ".env.production",
        ".env.test",
        "credentials.json",
        "secrets.json",
        "id_rsa",
        "id_ed25519",
        "id_ecdsa",
        ".npmrc",
        ".netrc",
        ".pypirc",
        "service-account.json",
    }
)

CREDENTIAL_EXTENSIONS = frozenset({".pem", ".key", ".p12", ".pfx", ".kdbx"})

PRIVATE_PATH_COMPONENTS = frozenset({"PRIVATE", ".private"})


@dataclass(frozen=True)
class SelectionRules:
    """Deterministic selection configuration. Budgets are per-analysis."""

    supported_languages: frozenset[str] = SUPPORTED_LANGUAGES
    max_file_bytes: int = 1_000_000
    max_total_selected_bytes: int = 8_000_000

    def language_for(self, path: str) -> str | None:
        lowered = path.lower()
        for extension, language in sorted(LANGUAGE_BY_EXTENSION.items()):
            if lowered.endswith(extension):
                return language
        return None

    def is_private_path(self, path: str) -> bool:
        return any(part in PRIVATE_PATH_COMPONENTS for part in path.split("/"))

    def is_credential_path(self, path: str) -> bool:
        parts = path.split("/")
        name = parts[-1].lower() if parts else ""
        return (
            name in CREDENTIAL_BASENAMES
            or any(name.endswith(ext) for ext in CREDENTIAL_EXTENSIONS)
        )
