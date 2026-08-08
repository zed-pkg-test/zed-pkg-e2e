#!/usr/bin/env python3
"""Cross-platform entrypoint for the independent GitOps dispatch canary."""

from __future__ import annotations

import gitops_dispatch_canary as canary


_original_combined = canary.combined


def platform_normalized_output(result):
    """Normalize only Windows' conventional executable suffix for assertions."""
    return _original_combined(result).replace("zed-gitops.exe", "zed-gitops")


canary.combined = platform_normalized_output


if __name__ == "__main__":
    raise SystemExit(canary.main())
