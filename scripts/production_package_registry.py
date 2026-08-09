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
from production_package_common import CertificationError, Package

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

def inspect_archive(data: bytes, expected_format: str) -> dict[str, Any]:
    members: list[str]
    if expected_format == 'zip':
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            members = archive.namelist()
            if archive.testzip() is not None:
                raise CertificationError('ZIP artifact failed CRC verification')
    else:
        with tarfile.open(fileobj=io.BytesIO(data), mode='r:*') as archive:
            members = archive.getnames()
    normalized = [member.lstrip('./') for member in members]
    if not any((member == '.zpkg.toml' or member.endswith('/.zpkg.toml') for member in normalized)):
        raise CertificationError('published artifact does not contain .zpkg.toml')
    return {'member_count': len(members), 'sample_members': members[:20]}

def metadata_for(registry: str, package: Package, token: str | None) -> dict[str, Any]:
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
    archive = inspect_archive(artifact, str(metadata.get('format', 'tar.gz')))
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
    lines = ['# Production package publish/install roundtrip', '', f"Status: **{report.get('status', 'unknown')}**", f"Packages: **{report.get('package_count', 0)}**", f"Registry: `{report.get('registry', '')}`", '', '| Order | Package | Version | Source SHA | Artifact SHA-256 | Installed | Frozen reinstall |', '| ---: | --- | --- | --- | --- | --- | --- |']
    installed = set(report.get('installed', []))
    frozen = set(report.get('frozen_reinstalled', []))
    for index, item in enumerate(report.get('packages', []), 1):
        lines.append(f"| {index} | `{item['package']}` | `{item['version']}` | `{item['source_sha']}` | `{item.get('artifact', {}).get('sha256', '')}` | {('yes' if item['package'] in installed else 'no')} | {('yes' if item['package'] in frozen else 'no')} |")
    if report.get('error'):
        lines.extend(['', '## Failure', '', '```text', str(report['error']), '```'])
    (evidence_dir / 'production-package-roundtrip.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
