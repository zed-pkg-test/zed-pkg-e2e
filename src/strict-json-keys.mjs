// Strictly reject duplicate JSON object names before JSON.parse can discard them.
// This preflight is not a substitute for JSON.parse's full syntax/type checks.
const whitespace = ch => ch === ' ' || ch === '\n' || ch === '\r' || ch === '\t';

export function rejectDuplicateJsonKeys(source, maxDepth = 128) {
  if (typeof source !== 'string' || !Number.isInteger(maxDepth) || maxDepth < 1)
    throw new Error('invalid JSON key admission input');
  if (source.charCodeAt(0) === 0xfeff)
    throw new Error('JSON BOM is not admitted');
  let i = 0;
  const frames = [{kind: 'root', state: 'value'}];
  const fail = message => { throw new Error('invalid or ambiguous JSON: ' + message); };
  const skipWhitespace = () => {
    while (i < source.length && whitespace(source[i])) ++i;
  };
  const stringToken = () => {
    if (source[i] !== '"') fail('expected string');
    const start = i++;
    let escaped = false;
    while (i < source.length) {
      const ch = source[i++];
      if (escaped) { escaped = false; continue; }
      if (ch === '\\') { escaped = true; continue; }
      if (ch === '"') {
        // Decode keys using JSON's own Unicode escape rules: "x" and
        // "\\u0078" have the same key identity.
        return source.slice(start, i);
      }
    }
    fail('unterminated string');
  };
  const markValue = frame => {
    frame.state = frame.kind === 'root' ? 'done' : 'commaOrEnd';
  };
  const startValue = frame => {
    if (i >= source.length) fail('missing value');
    const ch = source[i];
    markValue(frame);
    if (ch === '{' || ch === '[') {
      if (frames.length >= maxDepth + 1) fail('nesting limit exceeded');
      ++i;
      frames.push(ch === '{'
        ? {kind: 'object', state: 'keyOrEnd', names: new Set()}
        : {kind: 'array', state: 'valueOrEnd'});
    } else if (ch === '"') {
      stringToken();
    } else {
      // Skip one scalar token; the real JSON parser validates its spelling.
      const start = i;
      while (i < source.length && !whitespace(source[i]) &&
             source[i] !== ',' && source[i] !== '}' && source[i] !== ']') ++i;
      if (start === i) fail('expected scalar');
    }
  };
  while (frames.length) {
    skipWhitespace();
    const frame = frames[frames.length - 1];
    const ch = source[i];
    if (frame.kind === 'root') {
      if (frame.state === 'value') startValue(frame);
      else {
        if (i !== source.length) fail('trailing input');
        frames.pop();
      }
    } else if (frame.kind === 'object') {
      if (frame.state === 'keyOrEnd' || frame.state === 'key') {
        if (ch === '}' && frame.state === 'keyOrEnd') { ++i; frames.pop(); continue; }
        const spelling = stringToken();
        const key = JSON.parse(spelling);
        if (frame.names.has(key)) fail('duplicate object key');
        frame.names.add(key);
        frame.state = 'colon';
      } else if (frame.state === 'colon') {
        if (ch !== ':') fail('expected colon');
        ++i;
        frame.state = 'value';
      } else if (frame.state === 'value') {
        startValue(frame);
      } else {
        if (ch === '}') { ++i; frames.pop(); }
        else if (ch === ',') { ++i; frame.state = 'key'; }
        else fail('expected comma or object end');
      }
    } else {
      if (frame.state === 'valueOrEnd' && ch === ']') { ++i; frames.pop(); }
      else if (frame.state === 'value' || frame.state === 'valueOrEnd') startValue(frame);
      else if (ch === ']') { ++i; frames.pop(); }
      else if (ch === ',') { ++i; frame.state = 'value'; }
      else fail('expected comma or array end');
    }
  }
}
