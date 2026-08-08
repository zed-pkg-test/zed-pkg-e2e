#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected one replay marker, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def resolve(root: Path) -> None:
    lib = root / "src/lib.rs"
    replace_once(
        lib,
        "pub mod exec;\n",
        "pub mod exec;\npub mod external_subcommands;\n",
    )

    main = root / "src/main.rs"
    replace_once(
        main,
        "use zed_cli::{\n    cli::{\n",
        "use zed_cli::{\n    external_subcommands::{ExternalDispatch, external_route, run_external},\n    cli::{\n",
    )
    replace_once(
        main,
        '''    if let Some(code) = dev::dispatch(args.clone()) {
        std::process::exit(code);
    }

    let matches = cli_model::built_in_cli_command()
''',
        '''    if let Some(code) = dev::dispatch(args.clone()) {
        std::process::exit(code);
    }

    if let Some(route) = external_route(&args) {
        let code = match run_external(&route) {
            Ok(ExternalDispatch::Exited(code)) => code,
            Ok(ExternalDispatch::MissingKnown(message)) => {
                eprintln!("error: {message}");
                1
            }
            Err(error) => {
                eprintln!("error: {error:#}");
                1
            }
        };
        std::process::exit(code);
    }

    let matches = cli_model::built_in_cli_command()
''',
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    resolve(args.root.resolve(strict=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
