import fs from 'node:fs';
import path from 'node:path';

export function applyFleetManifestExtensions(manifest, manifestPath) {
  const extensionPath = path.join(path.dirname(manifestPath), 'test-org-fleet.extensions.json');
  if (!fs.existsSync(extensionPath)) return manifest;

  const extension = JSON.parse(fs.readFileSync(extensionPath, 'utf8'));
  if (extension.schemaVersion !== 1) {
    throw new Error(`fleet extension schemaVersion must be 1: ${extensionPath}`);
  }
  if (!Array.isArray(extension.pairs) || extension.pairs.length === 0) {
    throw new Error(`fleet extension pairs must be a non-empty array: ${extensionPath}`);
  }

  const patchedOrganizations = new Set();
  for (const pairExtension of extension.pairs) {
    if (typeof pairExtension.testOrg !== 'string' || pairExtension.testOrg.length === 0) {
      throw new Error(`fleet extension testOrg must be a non-empty string: ${extensionPath}`);
    }
    if (patchedOrganizations.has(pairExtension.testOrg)) {
      throw new Error(`duplicate fleet extension pair: ${pairExtension.testOrg}`);
    }
    patchedOrganizations.add(pairExtension.testOrg);

    const pair = manifest.pairs?.find((candidate) => candidate.testOrg === pairExtension.testOrg);
    if (!pair) throw new Error(`fleet extension targets unknown test organization: ${pairExtension.testOrg}`);
    if (pairExtension.sourceOrg && pairExtension.sourceOrg !== pair.sourceOrg) {
      throw new Error(`fleet extension source organization mismatch for ${pairExtension.testOrg}`);
    }
    if (!Array.isArray(pairExtension.repositories) || pairExtension.repositories.length === 0) {
      throw new Error(`fleet extension repositories must be non-empty for ${pairExtension.testOrg}`);
    }

    const names = new Set([
      ...(pair.existingRepositories ?? []),
      ...(pair.repositories ?? []).map((repository) => repository.name),
    ]);
    for (const repository of pairExtension.repositories) {
      if (!repository || typeof repository.name !== 'string' || repository.name.length === 0) {
        throw new Error(`fleet extension repository name is required for ${pairExtension.testOrg}`);
      }
      if (names.has(repository.name)) {
        throw new Error(`fleet extension duplicates repository ${pairExtension.testOrg}/${repository.name}`);
      }
      names.add(repository.name);
      pair.repositories.push(JSON.parse(JSON.stringify(repository)));
    }
  }

  return manifest;
}
