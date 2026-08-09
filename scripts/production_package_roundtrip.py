#!/usr/bin/env python3
from __future__ import annotations
import argparse
import os
import shutil
import traceback
from pathlib import Path
from typing import Any
from production_package_common import CertificationError, load_ledger, run
from production_package_source import checkout_source, parse_manifest
from production_package_registry import metadata_for, verify_install, write_evidence

def certify(args: argparse.Namespace) -> None:
    ledger_raw, packages = load_ledger(args.ledger)
    workspace = args.workspace.resolve()
    sources = workspace / 'sources'
    consumer = workspace / 'consumer'
    home = workspace / 'zed-home'
    for path in (sources, consumer, home, args.evidence):
        if path.exists():
            shutil.rmtree(path)
        path.mkdir(parents=True, exist_ok=True)
    cli_env = os.environ.copy()
    cli_env.update({'ZED_PKG_REGISTRY': args.registry.rstrip('/'), 'ZED_PKG_HOME': str(home), 'ZED_PKG_ALLOW_NO_MANIFEST': '1', 'ZED_PKG_INSTALL_MODE': 'copy'})
    if args.token:
        cli_env['ZED_PKG_TOKEN'] = args.token
    report: dict[str, Any] = {'schema': 1, 'status': 'running', 'registry': args.registry.rstrip('/'), 'package_count': len(packages), 'registry_api_sha': ledger_raw.get('registry_api_sha'), 'zed_cli_sha': ledger_raw.get('zed_cli_sha'), 'packages': [], 'installed': [], 'frozen_reinstalled': []}
    try:
        run([str(args.zed), '--help'], env=cli_env, timeout=120)
        for org in sorted({package.org for package in packages}):
            run([str(args.zed), 'org', 'claim', org], env=cli_env, timeout=120)
        checkouts: dict[str, Path] = {}
        for package in packages:
            source = checkout_source(package, sources)
            parse_manifest(source, package)
            checkouts[package.package] = source
            report['packages'].append({'package': package.package, 'version': package.version, 'source_sha': package.sha, 'tag': package.tag, 'dependencies': list(package.dependencies), 'source_url': package.source_url, 'publish': {}, 'idempotent_retry': {}, 'artifact': {}})
            write_evidence(args.evidence, report)
        rows_by_package = {row['package']: row for row in report['packages']}
        for package in packages:
            source = checkouts[package.package]
            before = run(['git', 'status', '--porcelain=v1', '--untracked-files=all'], cwd=source).stdout
            if before:
                raise CertificationError(f'{package.package}: dirty before publish:\n{before}')
            result = run([str(args.zed), 'publish', '--allow-dirty', '--skip-vcs-checks'], cwd=source, env=cli_env, timeout=1800)
            rows_by_package[package.package]['publish'] = {'returncode': result.returncode, 'duration_seconds': result.duration_seconds, 'stdout': result.stdout[-4000:], 'stderr': result.stderr[-4000:]}
            rows_by_package[package.package]['artifact'] = metadata_for(args.registry, package, args.token)
            write_evidence(args.evidence, report)
        for package in packages:
            source = checkouts[package.package]
            for generated in (source / '.zed', source / '.zed-pack'):
                if generated.exists():
                    shutil.rmtree(generated)
            result = run([str(args.zed), 'publish', '--allow-dirty', '--skip-vcs-checks'], cwd=source, env=cli_env, timeout=1800)
            rows_by_package[package.package]['idempotent_retry'] = {'returncode': result.returncode, 'duration_seconds': result.duration_seconds, 'stdout': result.stdout[-4000:], 'stderr': result.stderr[-4000:]}
            retry_artifact = metadata_for(args.registry, package, args.token)
            if retry_artifact['sha256'] != rows_by_package[package.package]['artifact']['sha256']:
                raise CertificationError(f'{package.package}: idempotent retry changed artifact digest')
            write_evidence(args.evidence, report)
        specs = [package.spec for package in packages]
        run([str(args.zed), 'install', *specs, '--skip-manifest'], cwd=consumer, env=cli_env, timeout=3600)
        expected = {package.package for package in packages}
        report['installed'] = verify_install(consumer, expected, 'initial install')
        write_evidence(args.evidence, report)
        run([str(args.zed), 'uninstall'], cwd=consumer, env=cli_env, timeout=1800)
        run([str(args.zed), 'install', '--frozen', '--skip-manifest'], cwd=consumer, env=cli_env, timeout=3600)
        report['frozen_reinstalled'] = verify_install(consumer, expected, 'frozen reinstall')
        report['status'] = 'success'
        write_evidence(args.evidence, report)
    except Exception as error:
        report['status'] = 'failure'
        report['error'] = f'{type(error).__name__}: {error}\n{traceback.format_exc()}'
        write_evidence(args.evidence, report)
        raise

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument('--ledger', type=Path, required=True)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--zed', type=Path, required=True)
    parser.add_argument('--registry', required=True)
    parser.add_argument('--token', default=os.environ.get('ZED_PKG_TOKEN', ''))
    return parser.parse_args()

if __name__ == '__main__':
    certify(parse_args())
