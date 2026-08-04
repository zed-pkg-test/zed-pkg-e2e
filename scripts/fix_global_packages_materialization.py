#!/usr/bin/env python3
"""Narrow compatibility repair for the current-main global package assembly.

The main materializer intentionally copies the reviewed feature files before
composing them with current main. This verifier preserves the current lockfile
import surface and normalizes the root-help adapter to the `Result<i32>` contract
expected by the modular dispatcher. It is idempotent and fails on unknown source
shapes rather than guessing.
"""

from __future__ import annotations

import argparse
from pathlib import Path


def patch_ops(product: Path) -> None:
    path = product / "src/ops.rs"
    text = path.read_text()
    complete = "use zed_interfaces::lockfile::{LockedPackage, Lockfile};"
    legacy = "use zed_interfaces::lockfile::Lockfile;"
    if complete in text:
        return
    if legacy not in text:
        raise SystemExit("src/ops.rs has an unknown lockfile import shape")
    path.write_text(text.replace(legacy, complete, 1))


def patch_global(product: Path) -> None:
    path = product / "src/global.rs"
    text = path.read_text()

    mapped_arm = "Route::RootHelp => Some(print_root_help().map(|()| 0)),"
    direct_arm = "Route::RootHelp => Some(print_root_help()),"
    if mapped_arm in text:
        text = text.replace(mapped_arm, direct_arm, 1)
    elif direct_arm not in text:
        raise SystemExit("src/global.rs has an unknown RootHelp dispatch shape")

    variants = (
        '''fn print_root_help() -> Result<()> {
    crate::completion::print_root_help()
}
''',
        '''fn print_root_help() -> Result<i32> {
    crate::completion::print_root_help()
}
''',
        '''fn print_root_help() -> Result<()> {
    let mut command = dev::augment_root_command(augment_root_command(crate::cli::Cli::command()));
    command.print_help().context("printing zed help")?;
    println!();
    Ok(())
}
''',
    )
    replacement = '''fn print_root_help() -> Result<i32> {
    crate::completion::print_root_help()?;
    Ok(0)
}
'''
    if replacement not in text:
        for variant in variants:
            if variant in text:
                text = text.replace(variant, replacement, 1)
                break
        else:
            raise SystemExit("src/global.rs has an unknown print_root_help shape")

    path.write_text(text)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--product", type=Path, required=True)
    args = parser.parse_args()
    product = args.product.resolve()
    patch_ops(product)
    patch_global(product)
    print("preserved LockedPackage import and normalized root-help result contract")


if __name__ == "__main__":
    main()
