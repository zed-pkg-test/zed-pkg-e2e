#!/usr/bin/env python3
from __future__ import annotations
import hashlib
import io
import json
import tarfile
import tomllib
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Any
from production_package_common import CertificationError, RegistryPackage

def api_json(url: str, token: str | None) -> dict[str, Any]:
    headers = {'Accept': 'application/json', 'User-Agent': 'zed-production-roundtrip/1'}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        body = error.read().decode('utf-8', errors='replace')
        raise CertificationError(f'HTTP {error.code} for {url}: {body[:1000]}') from error

def api_bytes(url: str, token: str | None) -> bytes:
    headers = {'Accept': 'application/octet-stream', 'User-Agent': 'zed-production-roundtrip/1'}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        body = error.read().decode('utf-8', errors='replace')
        raise CertificationError(f'HTTP {error.code} for {url}: {body[:1000]}') from error

def _verify_embedded_manifest(raw: bytes, package: RegistryPackage) -> dict[str, Any]:
    try:
        document = tomllib.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise CertificationError(f'{package.package}: artifact contains an invalid .zpkg.toml') from error
    declared = document.get('package')
    if not isinstance(declared, dict):
        raise CertificationError(f'{package.package}: artifact manifest is missing [package]')
    actual = (declared.get('org'), declared.get('name'), declared.get('version'))
    expected = (package.org, package.name, package.version)
    if actual != expected:
        raise CertificationError(f'{package.package}: artifact manifest identity {actual!r} != {expected!r}')
    targets = document.get('targets')
    if package.target is not None and isinstance(targets, dict) and targets:
        raise CertificationError(f'{package.package}: derived target artifact retained nested [targets]')
    return {'org': package.org, 'name': package.name, 'version': package.version, 'target': package.target}

def inspect_archive(data: bytes, expected_format: str, package: RegistryPackage) -> dict[str, Any]:
    members: list[str]
    manifest_bytes: bytes | None = None
    if expected_format == 'zip':
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            members = archive.namelist()
            if archive.testzip() is not None:
                raise CertificationError('ZIP artifact failed CRC verification')
            manifest_names = [name for name in members if name.lstrip('./') == '.zpkg.toml' or name.lstrip('./').endswith('/.zpkg.toml')]
            if len(manifest_names) != 1:
                raise CertificationError(f'{package.package}: expected one artifact manifest, found {manifest_names!r}')
            manifest_bytes = archive.read(manifest_names[0])
    else:
        with tarfile.open(fileobj=io.BytesIO(data), mode='r:*') as archive:
            members = archive.getnames()
            manifest_names = [name for name in members if name.lstrip('./') == '.zpkg.toml' or name.lstrip('./').endswith('/.zpkg.toml')]
            if len(manifest_names) != 1:
                raise CertificationError(f'{package.package}: expected one artifact manifest, found {manifest_names!r}')
            member = archive.extractfile(manifest_names[0])
            if member is None:
                raise CertificationError(f'{package.package}: artifact manifest is not a regular file')
            manifest_bytes = member.read()
    assert manifest_bytes is not None
    embedded = _verify_embedded_manifest(manifest_bytes, package)
    return {'member_count': len(members), 'sample_members': members[:20], 'embedded_manifest': embedded}

def metadata_for(registry: str, package: RegistryPackage, token: str | None) -> dict[str, Any]:
    parts = [urllib.parse.quote(value, safe='') for value in (package.org, package.name, package.version)]
    url = f"{registry.rstrip('/')}/v1/packages/{parts[0]}/{parts[1]}/versions/{parts[2]}"
    metadata = api_json(url, token)
    for field, expected in (('org', package.org), ('name', package.name), ('version', package.version)):
        if metadata.get(field) != expected:
            raise CertificationError(f'{package.package}: metadata {field}={metadata.get(field)!r}, expected {expected!r}')
    digest = metadata.get('sha256')
    if not isinstance(digest, str) or len(digest) != 64:
        raise CertificationError(f'{package.package}: invalid metadata sha256 {digest!r}')
    download_url = metadata.get('download_url')
    if not isinstance(download_url, str) or not download_url:
        raise CertificationError(f'{package.package}: metadata missing download_url')
    resolved_download = urllib.parse.urljoin(f"{registry.rstrip('/')}/", download_url)
    artifact = api_bytes(resolved_download, token)
    actual_digest = hashlib.sha256(artifact).hexdigest()
    if actual_digest != digest:
        raise CertificationError(f'{package.package}: downloaded artifact sha256 {actual_digest} != metadata {digest}')
    archive = inspect_archive(artifact, str(metadata.get('format', 'tar.gz')), package)
    return {'metadata_url': url, 'download_url': resolved_download, 'sha256': digest, 'size': len(artifact), 'format': metadata.get('format'), **archive}

def installed_coordinates(root: Path) -> set[str]:
    coordinates: set[str] = set()
    for manifest in root.rglob('.zpkg.toml'):
        try:
            document = tomllib.loads(manifest.read_text(encoding='utf-8'))
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
            continue
        package = document.get('package')
        if isinstance(package, dict) and isinstance(package.get('org'), str) and isinstance(package.get('name'), str):
            coordinates.add(f"{package['org']}/{package['name']}")
    return coordinates

def verify_install(root: Path, expected: set[str], phase: str) -> list[str]:
    actual = installed_coordinates(root)
    missing = sorted(expected - actual)
    if missing:
        manifests = sorted((str(path.relative_to(root)) for path in root.rglob('.zpkg.toml')))
        raise CertificationError(f'{phase}: installed tree is missing {missing}; discovered manifests={manifests[:100]}')
    return sorted(actual & expected)

def write_evidence(evidence_dir: Path, report: dict[str, Any]) -> None:
    evidence_dir.mkdir(parents=True, exist_ok=True)
    (evidence_dir / 'production-package-roundtrip.json').write_text(json.dumps(report, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    lines = [
        '# Production package publish/install roundtrip',
        '',
        f"Status: **{report.get('status', 'unknown')}**",
        f"Logical source repositories: **{report.get('logical_package_count', report.get('package_count', 0))}**",
        f"Concrete registry packages: **{report.get('registry_package_count', 0)}**",
        f"Registry: `{report.get('registry', '')}`",
        '',
        '| Order | Source repository | Registry package | Target | Version | Source SHA | Artifact SHA-256 | Installed | Frozen reinstall |',
        '| ---: | --- | --- | --- | --- | --- | --- | --- | --- |',
    ]
    installed = set(report.get('installed', []))
    frozen = set(report.get('frozen_reinstalled', []))
    order = 0
    for item in report.get('packages', []):
        for concrete in item.get('registry_packages', []):
            order += 1
            coordinate = concrete['package']
            lines.append(
                f"| {order} | `{item['package']}` | `{coordinate}` | `{concrete.get('target') or 'single'}` | `{concrete['version']}` | `{item['source_sha']}` | `{concrete.get('artifact', {}).get('sha256', '')}` | {('yes' if coordinate in installed else 'no')} | {('yes' if coordinate in frozen else 'no')} |"
            )
    if report.get('error'):
        lines.extend(['', '## Failure', '', '```text', str(report['error']), '```'])
    (evidence_dir / 'production-package-roundtrip.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
