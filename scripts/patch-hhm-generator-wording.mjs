#!/usr/bin/env node
import fs from 'node:fs';
import path from 'node:path';

const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');
const file = path.join(root, 'scripts', 'add-hhm-test-fleet.mjs');
const before = fs.readFileSync(file, 'utf8');
const needle = "        'no real door credentials',";
const replacement = "        'synthetic access-grant fixtures only',";
const oldOccurrences = before.split(needle).length - 1;
const newOccurrences = before.split(replacement).length - 1;

if (oldOccurrences === 1 && newOccurrences === 0) {
  fs.writeFileSync(file, before.replace(needle, replacement));
} else if (oldOccurrences === 0 && newOccurrences === 1) {
  console.log('privacy wording is already corrected');
} else {
  throw new Error(
    `unexpected privacy wording state: old=${oldOccurrences}, new=${newOccurrences}`,
  );
}
