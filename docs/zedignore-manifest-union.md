# DEN-3018 `.zedignore` and manifest union certification

This independent canary certifies the exact `zed-pkg/zed-cli` candidate that
makes the existing two-source publish-ignore contract visible to users.

## Immutable product identity

```text
comparison base: 1ab18fcb2ff884e82af4cac4513d7b983a23c84a
candidate:       4b27a48976686e2fd309926fafd0a2411de9fb87
product PR:      zed-pkg/zed-cli#247
```

The workflow requires the comparison base to be an ancestor of the candidate
and the complete base-to-candidate delta to contain exactly:

```text
docs/publish-ignore.md
src/ops_entry.rs
```

It never follows a mutable product branch.

## Certified contract

Ubuntu 24.04, macOS 15, and Windows Server 2025 each run formatting, the focused
Rust unit tests, strict Clippy, and a locked release build. The resulting binary
then packs disposable local fixtures and inspects the real `tar.gz` members.

The adversarial fixture proves that:

- `[publish].exclude` is evaluated before `.zedignore`;
- both active sources produce exactly one union warning;
- opposite-polarity rules for the normalized `target` family produce one
  conflict warning and the later `.zedignore` negation wins;
- same-polarity duplicates with case and `**/` spelling differences do not
  produce a contradiction warning;
- manifest-only and `.zedignore`-only projects do not produce a dual-source
  warning;
- manifest exclusions, `.zedignore` exclusions, and the `.zedignore` control
  file stay out of the artifact;
- a deliberately retained hidden file is not removed by a blanket-dotfile
  policy; and
- `.zpkg.toml` plus `LICENSE` remain present even when explicit exclusions match
  them.

The process receives a disposable Zed home and an unreachable loopback registry.
It performs no publication, registry write, package install, provider execution,
Cloudflare request, credential read, or persistent namespace mutation. Uploaded
evidence contains only immutable commit identities, runner identity, binary and
fixture-archive digests, boolean assertion results, and the final result.

## Promotion order

Keep both pull requests draft until exact-head product and independent test-org
checks are terminal-green and review threads are clear. Merge the independent
certification first; promote `zed-pkg/zed-cli#247` only while its final candidate
still matches the certified SHA and its comparison-base delta remains exact.

Linear: DEN-3018
