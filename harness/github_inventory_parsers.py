#!/usr/bin/env python3
"""Combined source parser mixin for DEN-2957."""

from github_inventory_zed_parsers import ZedParsingMixin
from github_inventory_source_parsers import SourceParsingMixin


class InventoryParsingMixin(ZedParsingMixin, SourceParsingMixin):
    pass
