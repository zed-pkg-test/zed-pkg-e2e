#!/usr/bin/env node
import fs from 'node:fs';
import path from 'node:path';

const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');
const file = path.join(root, 'scripts', 'add-hhm-test-fleet.mjs');
const before = fs.readFileSync(file, 'utf8');
const needle = "        'no real door credentials',";
const replacement = "        'synthetic access-grant fixtures only',";
const occurrences = before.split(needle).length - 1;
if (occurrences !== 1) {
  throw new Error(`expected one privacy wording occurrence, found ${occurrences}`);
}
fs.writeFileSync(file, before.replace(needle, replacement));
