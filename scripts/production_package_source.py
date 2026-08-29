#!/usr/bin/env python3
from __future__ import annotations
import os
import shutil
import tomllib
from pathlib import Path
from typing import Any
from urllib.parse import quote
from production_package_common import CertificationError, Package, RegistryPackage, run

def _credential_file(root: Path) -> Path | None:
    token = os.environ.get('ZED_PKG_GITHUB_TOKEN', '').strip()
    if not token:
        return None
    path = root / '.zpkg-git-credentials'
    path.write_text(
        f'https://x-access-token:{quote(token, safe="")}@github.com\n',
        encoding='utf-8',
    )
    path.chmod(0o600)
    return path

def _git(
    arguments: list[str],
    *,
    cwd: Path,
    credential_file: Path | None,
    check: bool = True,
    timeout: int = 1800,
):
    command = ['git']
    if credential_file is not None:
        command.extend(['-c', f'credential.helper=store --file={credential_file}'])
    command.extend(arguments)
    env = os.environ.copy()
    env['GIT_TERMINAL_PROMPT'] = '0'
    return run(command, cwd=cwd, env=env, check=check, timeout=timeout)

def checkout_source(package: Package, root: Path) -> Path:
    target = root / package.github_owner / package.repo
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        shutil.rmtree(target)
    target.mkdir()
    credential_file = _credential_file(root)
    try:
        _git(['init', '--quiet'], cwd=target, credential_file=credential_file)
        _git(['config', 'core.autocrlf', 'false'], cwd=target, credential_file=credential_file)
        _git(['remote', 'add', 'origin', package.source_url], cwd=target, credential_file=credential_file)
        fetch = _git(
            ['fetch', '--quiet', '--no-tags', '--depth=1', 'origin', package.sha],
            cwd=target,
            credential_file=credential_file,
            check=False,
            timeout=900,
        )
        if fetch.returncode != 0:
            ref = f'refs/tags/{package.tag}' if package.tag else 'refs/heads/main'
            _git(
                ['fetch', '--quiet', '--depth=1', 'origin', ref],
                cwd=target,
                credential_file=credential_file,
                timeout=900,
            )
            fetched = _git(
                ['rev-parse', 'FETCH_HEAD^{commit}'],
                cwd=target,
                credential_file=credential_file,
            ).stdout.strip()
            if fetched != package.sha:
                raise CertificationError(
                    f'{package.package}: fallback ref {ref} resolved to {fetched}, expected {package.sha}'
                )
        _git(
            ['checkout', '--quiet', '--detach', package.sha],
            cwd=target,
            credential_file=credential_file,
        )
        actual = _git(
            ['rev-parse', 'HEAD'],
            cwd=target,
            credential_file=credential_file,
        ).stdout.strip()
        if actual != package.sha:
            raise CertificationError(
                f'{package.package}: checkout resolved to {actual}, expected {package.sha}'
            )
        if package.tag:
            _git(
                [
                    'fetch',
                    '--quiet',
                    '--depth=1',
                    'origin',
                    f'refs/tags/{package.tag}:refs/tags/{package.tag}',
                ],
                cwd=target,
                credential_file=credential_file,
                timeout=900,
            )
            tagged = _git(
                ['rev-parse', f'{package.tag}^{{commit}}'],
                cwd=target,
                credential_file=credential_file,
            ).stdout.strip()
            if tagged != package.sha:
                raise CertificationError(
                    f'{package.package}: tag {package.tag} resolves to {tagged}, expected {package.sha}'
                )
        status = _git(
            ['status', '--porcelain=v1', '--untracked-files=all'],
            cwd=target,
            credential_file=credential_file,
        ).stdout
        if status:
            raise CertificationError(f'{package.package}: checkout is not clean:\n{status}')
        return target
    finally:
        if credential_file is not None:
            credential_file.unlink(missing_ok=True)

def _registry_packages(document: dict[str, Any], package: Package) -> tuple[RegistryPackage, ...]:
    targets = document.get('targets')
    if targets is None or targets == {}:
        return (
            RegistryPackage(
                logical_package=package.package,
                org=package.org,
                name=package.name,
                version=package.version,
                target=None,
            ),
        )
    if not isinstance(targets, dict):
        raise CertificationError(f'{package.package}: [targets] must be a table')
    concrete: list[RegistryPackage] = []
    for target, section in sorted(targets.items()):
        if not isinstance(target, str) or not target or '/' in target:
            raise CertificationError(f'{package.package}: invalid target key {target!r}')
        if not isinstance(section, dict):
            raise CertificationError(f'{package.package}: target {target!r} must be a table')
        explicit_name = section.get('name')
        if explicit_name is not None and (not isinstance(explicit_name, str) or not explicit_name):
            raise CertificationError(f'{package.package}: target {target!r} has invalid name')
        # The CLI canonicalizes any target spanning the repository root (`dir = "."`)
        # to the logical package coordinate, regardless of the target key. This covers
        # both `[targets.repository]` packages and language-native roots such as
        # zed-lock's `[targets.rust]` without inventing a `-rust` registry package.
        is_root_spanning_target = section.get('dir') == '.'
        if is_root_spanning_target:
            if explicit_name is not None and explicit_name != package.name:
                raise CertificationError(
                    f'{package.package}: repository-root target must use canonical package name '
                    f'{package.name!r}, not {explicit_name!r}'
                )
            name = package.name
        else:
            name = explicit_name or f'{package.name}-{target}'
        if '/' in name:
            raise CertificationError(f'{package.package}: target {target!r} has invalid package name {name!r}')
        concrete.append(
            RegistryPackage(
                logical_package=package.package,
                org=package.org,
                name=name,
                version=package.version,
                target=target,
            )
        )
    coordinates = [item.package for item in concrete]
    if len(coordinates) != len(set(coordinates)):
        raise CertificationError(f'{package.package}: targets publish duplicate package identities {coordinates!r}')
    return tuple(concrete)

def parse_manifest(path: Path, package: Package) -> tuple[dict[str, Any], tuple[RegistryPackage, ...]]:
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
    return document, _registry_packages(document, package)
