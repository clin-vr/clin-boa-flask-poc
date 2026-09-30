"""Enumerate every third-party library the codebase actually imports.

The library list has to be validated bank-side, so it needs to be accurate and
provable rather than hand-maintained. This walks the AST of every source file
and reports non-stdlib imports, with the modules that import them.

    python scripts/list_dependencies.py
    python scripts/list_dependencies.py --include-tests

Note: local imports inside functions (the optional parsers, the auth strategies)
are picked up too, which is the point — those are exactly the ones that are easy
to forget when compiling a list for approval.
"""

from __future__ import annotations

import argparse
import ast
import sys
from collections import defaultdict
from pathlib import Path

# Anything importable from a bare CPython install needs no approval.
STDLIB = set(getattr(sys, "stdlib_module_names", set())) | {
    "__future__", "typing", "dataclasses", "abc", "enum", "pathlib", "datetime",
    "hashlib", "json", "csv", "io", "re", "uuid", "logging", "threading", "time",
    "argparse", "collections", "shutil", "os", "sys", "xml", "urllib",
}

# import name -> the distribution you'd actually request
DISTRIBUTIONS = {
    "flask": "Flask",
    "requests": "requests",
    "urllib3": "urllib3",
    "requests_ntlm": "requests-ntlm",
    "requests_kerberos": "requests-kerberos",
    "openpyxl": "openpyxl",
    "docx": "python-docx",
    "pdfplumber": "pdfplumber",
    "pypdf": "pypdf",
    "lxml": "lxml",
    "yaml": "PyYAML",
    "oracledb": "oracledb",
    "pytest": "pytest",
}


LOCAL_MODULES = {"evidence_api", "generate_samples", "app", "conftest"}


def top_level(name: str) -> str:
    """Return the top-level package of a dotted module name."""
    return name.split(".")[0]


def imports_in(path: Path) -> set[str]:
    """Return the top-level modules imported anywhere in a file, skipping relative imports."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(top_level(alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:  # skip relative imports
                found.add(top_level(node.module))
    return found


def main() -> int:
    """Print the third-party imports under api/ and sharepoint-mock/, with the files that use them.

    Returns 1 when an import has no mapped distribution, otherwise 0.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--include-tests", action="store_true")
    args = parser.parse_args()

    root = Path(__file__).resolve().parent.parent
    targets = sorted((root / "api").rglob("*.py")) + sorted((root / "sharepoint-mock").rglob("*.py"))
    if args.include_tests:
        targets += sorted((root / "tests").rglob("*.py"))

    usage: dict[str, set[str]] = defaultdict(set)
    for path in targets:
        for module in imports_in(path):
            if module in STDLIB or module in LOCAL_MODULES:
                continue
            usage[module].add(str(path.relative_to(root)))

    if not usage:
        print("No third-party imports found.")
        return 0

    width = max(len(m) for m in usage) + 2
    print(f"{'import':<{width}}{'distribution':<22}used by")
    print("-" * (width + 22 + 40))
    for module in sorted(usage):
        dist = DISTRIBUTIONS.get(module, f"?? unmapped: {module}")
        files = ", ".join(sorted(Path(f).name for f in usage[module]))
        print(f"{module:<{width}}{dist:<22}{files}")

    print(f"\n{len(usage)} third-party imports across {len(targets)} files.")
    unmapped = [m for m in usage if m not in DISTRIBUTIONS]
    if unmapped:
        print(f"WARNING: no distribution mapped for: {', '.join(unmapped)}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
