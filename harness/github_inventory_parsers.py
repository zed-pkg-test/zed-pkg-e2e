#!/usr/bin/env python3
"""Combined source parser mixin for DEN-2957 and DEN-2997."""

from github_inventory_zed_workspace import WorkspaceZedParsingMixin
from github_inventory_source_parsers import SourceParsingMixin


class InventoryParsingMixin(WorkspaceZedParsingMixin, SourceParsingMixin):
    pass
