#!/usr/bin/env python3
"""Combined source parser mixin for DEN-2957 and DEN-2997."""

import github_inventory_core as _core
import github_inventory_zed_parsers as _zed


def _parse_requirement(value: str, label: str, limits: _core.Limits) -> str:
    """Preserve the existing Zed parser's bounded opaque requirement semantics."""

    _core._bounded_text(value, label, limits.max_field_bytes)
    return value


# The historical parser keeps this operation inline. Expose the same behavior
# before importing the workspace extension so stacked code can reuse one contract.
_zed._parse_requirement = _parse_requirement

from github_inventory_zed_workspace import WorkspaceZedParsingMixin  # noqa: E402
from github_inventory_source_parsers import SourceParsingMixin  # noqa: E402


class InventoryParsingMixin(WorkspaceZedParsingMixin, SourceParsingMixin):
    pass
