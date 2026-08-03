from pathlib import Path


# One-shot exact-head repin for the workspace install implementation.
OLD = "67c01b83c834ea14af996613f1a3dc0802a2fbd3"
NEW = "56e597b35deccc82bff7deee807f459a1def537d"
WORKFLOWS = [
    Path(".github/workflows/recursive-installs.yml"),
    Path(".github/workflows/recursive-installs-stress.yml"),
    Path(".github/workflows/install-recovery.yml"),
    Path(".github/workflows/org-app-installs.yml"),
]

for path in WORKFLOWS:
    text = path.read_text(encoding="utf-8")
    occurrences = text.count(OLD)
    if occurrences != 2:
        raise RuntimeError(f"{path}: expected two immutable pin occurrences, found {occurrences}")
    path.write_text(text.replace(OLD, NEW), encoding="utf-8")
