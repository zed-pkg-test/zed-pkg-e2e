// Host-only proof reference for language/opaque-newtypes-bounds-const-generics-v1.md.
// This is NOT an AttoLang parser, executable guest, or evidence of backend parity.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const contract = JSON.parse(readFileSync(new URL('../contracts/opaque-newtypes-bounds-const-generics-v1.json', import.meta.url), 'utf8'));
const I64_MIN = -(1n << 63n), I64_MAX = (1n << 63n) - 1n;
const U64_MAX = (1n << 64n) - 1n;
const MAX_ELEMENTS = BigInt(contract.reference_profile.max_elements);

function exact_int(text) {
  if (typeof text !== 'string' || !/^-?(0|[1-9][0-9]*)$/.test(text))
    throw new Error('E_CONST_GENERIC_ARG');
  return BigInt(text);
}
function i64(text) {
  const v=exact_int(text);
  if (v < I64_MIN || v > I64_MAX) throw new Error('E_RANGE_INVALID');
  return v;
}
function normalize_interval(c) {
  const lo=i64(c.lower), hi=i64(c.upper);
  if (lo > hi) throw new Error('E_RANGE_INVALID');
  const excluded = new Set((c.excluded ?? []).map(v => i64(v)).filter(v => v >= lo && v <= hi));
  if (BigInt(excluded.size) === (hi - lo + 1n)) throw new Error('E_RANGE_INVALID');
  return {lo,hi,excluded};
}
function accepts_value(c) {
  const interval=normalize_interval(c);
  try {
    const v=i64(c.value);
    return v >= interval.lo && v <= interval.hi && !interval.excluded.has(v);
  } catch (err) {
    if (err.message === 'E_RANGE_INVALID') return false;
    throw err;
  }
}
function const_elements(c) {
  const row=exact_int(c.row), column=exact_int(c.column);
  if (row < 0n || column < 0n || row > U64_MAX || column > U64_MAX)
    throw new Error('E_CONST_GENERIC_ARG');
  const product = row * column;
  if (product > U64_MAX) throw new Error('E_CONST_OVERFLOW');
  if (product > MAX_ELEMENTS) throw new Error('E_CONST_LAYOUT_BUDGET');
  return product.toString();
}

test('contract records only target behavior with no fabricated backend verification', () => {
  assert.equal(contract.schema, 'atto.opaque-newtypes-bounds-const-generics.v1');
  assert.equal(contract.status, 'TARGET_NOT_EXECUTED');
  assert.ok(Object.values(contract.backend_status).every(s => s === 'NOT_VERIFIED'));
  assert.equal(contract.reference_profile.signed_integer_bits, 64);
  assert.equal(contract.reference_profile.usize_bits, 64);
});

test('fully-qualified nominal identity survives same-base and imported-name comparisons', () => {
  for (const c of contract.nominal_cases)
    assert.equal(c.left === c.right,c.compatible,c.id);
  assert.equal(contract.nominal_cases.length, 4);
});

test('exact integers and closed interval/exclusion boundaries', () => {
  for (const c of contract.range_cases)
    assert.equal(accepts_value(c),c.valid,c.id);
  for (const c of contract.invalid_interval_cases)
    assert.throws(() => normalize_interval(c),{message:c.error},c.id);
  for (const invalid of ['01','1.0','1e3','+1','-0x2','', ' 2 ', '9_000', 'NaN'])
    assert.throws(() => i64(invalid),undefined,invalid);
  assert.equal(i64('-9223372036854775808'),I64_MIN);
  assert.equal(i64('9223372036854775807'),I64_MAX);
});

test('const dimensions use exact arithmetic, bounded natural values and no runtime substitution', () => {
  for (const c of contract.dimension_cases)
    if (c.error) assert.throws(()=>const_elements(c),{message:c.error},c.id);
    else assert.equal(const_elements(c),c.elements,c.id);
  assert.equal(const_elements({row:'4',column:'4'}),'16');
  assert.equal(const_elements({row:'2',column:'8'}),'16');
  assert.throws(()=>const_elements({row:'4',column:'1.5'}),/E_CONST_GENERIC_ARG/);
});

test('const specialization key includes full shape and element type, not byte count', () => {
  for (const c of contract.shape_cases)
    assert.equal(JSON.stringify(c.left)===JSON.stringify(c.right),c.compatible,c.id);
  assert.equal(contract.shape_cases[1].left[1]*contract.shape_cases[1].left[2],
               contract.shape_cases[1].right[1]*contract.shape_cases[1].right[2]);
});

test('failed checked construction never produces a nominal value in the host reference', () => {
  // Deliberately no public nominal constructor: success carries authenticated proof data.
  const token = Symbol('compiler-owned-proof');
  const make = (type, rules, raw) =>
    accepts_value({...rules,value:raw}) ? {type,raw,token} : {error:'E_RANGE_INVALID'};
  const port = make('pkg/Port:i64',{lower:'1',upper:'65535'},'8080');
  assert.equal(port.token,token);
  const bad = make('pkg/Port:i64',{lower:'1',upper:'65535'},'65536');
  assert.deepEqual(bad,{error:'E_RANGE_INVALID'});
  assert.equal(bad.token,undefined);
  assert.notEqual(make('pkg/UserId:i64',{lower:'1',upper:'65535'},'8080').type,port.type);
});
