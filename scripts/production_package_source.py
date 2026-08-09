#!/usr/bin/env python3
from __future__ import annotations
import shutil
import tomllib
from pathlib import Path
from typing import Any
from production_package_common import CertificationError, Package, run

def checkout_source(package: Package, root: Path) -> Path:
    target = root / package.github_owner / package.repo
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        shutil.rmtree(target)
    target.mkdir()
    run(['git', 'init', '--quiet'], cwd=target)
    run(['git', 'config', 'core.autocrlf', 'false'], cwd=target)
    run(['git', 'remote', 'add', 'origin', package.source_url], cwd=target)
    fetch = run(['git', 'fetch', '--quiet', '--no-tags', '--depth=1', 'origin', package.sha], cwd=target, check=False, timeout=900)
    if fetch.returncode != 0:
        ref = f'refs/tags/{package.tag}' if package.tag else 'refs/heads/main'
        run(['git', 'fetch', '--quiet', '--depth=1', 'origin', ref], cwd=target, timeout=900)
        fetched = run(['git', 'rev-parse', 'FETCH_HEAD^{commit}'], cwd=target).stdout.strip()
        if fetched != package.sha:
            raise CertificationError(f'{package.package}: fallback ref {ref} resolved to {fetched}, expected {package.sha}')
    run(['git', 'checkout', '--quiet', '--detach', package.sha], cwd=target)
    actual = run(['git', 'rev-parse', 'HEAD'], cwd=target).stdout.strip()
    if actual != package.sha:
        raise CertificationError(f'{package.package}: checkout resolved to {actual}, expected {package.sha}')
    if package.tag:
        run(['git', 'fetch', '--quiet', '--depth=1', 'origin', f'refs/tags/{package.tag}:refs/tags/{package.tag}'], cwd=target, timeout=900)
        tagged = run(['git', 'rev-parse', f'{package.tag}^{{commit}}'], cwd=target).stdout.strip()
        if tagged != package.sha:
            raise CertificationError(f'{package.package}: tag {package.tag} resolves to {tagged}, expected {package.sha}')
    status = run(['git', 'status', '--porcelain=v1', '--untracked-files=all'], cwd=target).stdout
    if status:
        raise CertificationError(f'{package.package}: checkout is not clean:\n{status}')
    return target

def parse_manifest(path: Path, package: Package) -> dict[str, Any]:
    manifest_path = path / '.zpkg.toml'
    if not manifest_path.is_file():
        raise CertificationError(f'{package.package}: missing root .zpkg.toml')
    document = tomllib.loads(manifest_path.read_text(encoding='utf-8'))
    declared = document.get('package')
    if not isinstance(declared, dict):
        raise CertificationError(f'{package.package}: missing [package]')
    values = (declared.get('org'), declared.get('name'), declared.get('version'))
    expected = (package.org, package.name, package.version)
    if values != expected:
        raise CertificationError(f'{package.package}: manifest identity {values!r} does not match {expected!r}')
    repository = declared.get('repository')
    expected_url = f'https://github.com/{package.github_owner}/{package.repo}'.casefold()
    if not isinstance(repository, dict) or not isinstance(repository.get('url'), str):
        raise CertificationError(f'{package.package}: missing package.repository.url')
    actual_url = repository['url'].removesuffix('.git').rstrip('/').casefold()
    if actual_url != expected_url:
        raise CertificationError(f'{package.package}: repository URL {actual_url!r} does not match {expected_url!r}')
    dependencies = document.get('dependencies')
    manifest_dependencies = tuple(sorted(dependencies)) if isinstance(dependencies, dict) else ()
    if manifest_dependencies != tuple(sorted(package.dependencies)):
        raise CertificationError(f'{package.package}: dependency ledger mismatch: manifest={manifest_dependencies!r}, ledger={tuple(sorted(package.dependencies))!r}')
    return document
