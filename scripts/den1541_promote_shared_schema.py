#!/usr/bin/env python3
"""Apply the reviewed shared-schema package promotion to lifecycle.py."""

from pathlib import Path

path = Path("scripts/lifecycle.py")
source = path.read_text(encoding="utf-8")

old_non_packages = 'NON_PACKAGE_REPOS = {"shared-schema", "zed-pkg-e2e"}'
new_non_packages = 'NON_PACKAGE_REPOS = {"zed-pkg-e2e"}'
if old_non_packages not in source:
    raise SystemExit("expected shared-schema non-package classification was not found")
source = source.replace(old_non_packages, new_non_packages, 1)

marker = '    "zed-pkg-test/gleam-lib": ("gleam-lib", "."),\n'
addition = marker + '    "zedtest/shared-schema": ("shared-schema", "."),\n'
if marker not in source:
    raise SystemExit("package source insertion marker was not found")
if '"zedtest/shared-schema":' in source:
    raise SystemExit("shared-schema package source already exists")
source = source.replace(marker, addition, 1)

path.write_text(source, encoding="utf-8")
