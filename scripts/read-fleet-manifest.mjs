import fs from 'node:fs';
import path from 'node:path';
import zlib from 'node:zlib';

export function readFleetManifest(manifestPath) {
  let bytes;
  if (fs.existsSync(manifestPath)) {
    bytes = fs.readFileSync(manifestPath);
  } else {
    const partsDir = `${manifestPath}.b64.parts`;
    const encoded = fs.readdirSync(partsDir).sort().map((name) => fs.readFileSync(path.join(partsDir, name), 'utf8').trim()).join('');
    bytes = Buffer.from(encoded, 'base64');
  }
  return JSON.parse((manifestPath.endsWith('.gz') ? zlib.gunzipSync(bytes) : bytes).toString('utf8'));
}
