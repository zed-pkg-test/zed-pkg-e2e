#!/usr/bin/env python3
"""Narrow compatibility repair for the current-main global package assembly.

The main materializer intentionally copies the reviewed feature files before
composing them with current main. This verifier preserves the current lockfile
import surface, normalizes the root-help adapter to the `Result<i32>` contract
expected by the modular dispatcher, boxes the large install payload so the
public command enum remains Clippy-clean, and removes a redundant Zed package
dependency while Cargo still owns that dependency through an immutable Git pin.
It is idempotent and fails on unknown source shapes rather than guessing.
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

    boxed_variant = "    Install(Box<GlobalInstallArgs>),"
    plain_variant = "    Install(GlobalInstallArgs),"
    if boxed_variant not in text:
        if plain_variant not in text:
            raise SystemExit("src/global.rs has an unknown GlobalAction::Install shape")
        text = text.replace(plain_variant, boxed_variant, 1)

    boxed_match = (
        "            GlobalAction::Install(options) => install(&cfg, &bin_dir, *options),"
    )
    plain_match = (
        "            GlobalAction::Install(options) => install(&cfg, &bin_dir, options),"
    )
    if boxed_match not in text:
        if plain_match not in text:
            raise SystemExit("src/global.rs has an unknown install dispatch shape")
        text = text.replace(plain_match, boxed_match, 1)

    path.write_text(text)


def patch_package_manifest(product: Path) -> None:
    path = product / ".zpkg.toml"
    text = path.read_text()
    redundant = '''[dependencies]
"zed-pkg/zed-interfaces" = "^0.1.0"

'''
    if redundant in text:
        text = text.replace(redundant, "", 1)
    elif "zed-pkg/zed-interfaces" in text:
        raise SystemExit(".zpkg.toml has an unknown zed-interfaces dependency shape")
    path.write_text(text)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--product", type=Path, required=True)
    args = parser.parse_args()
    product = args.product.resolve()
    patch_ops(product)
    patch_global(product)
    patch_package_manifest(product)
    print(
        "preserved current-main imports, normalized root help, boxed the install payload, and aligned package metadata with Cargo"
    )


if __name__ == "__main__":
    main()
