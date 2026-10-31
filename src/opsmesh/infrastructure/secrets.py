"""Resolve explicitly configured environment credentials at execution time."""

import os
import re

from opsmesh.framework.errors import OpsMeshError


class EnvironmentSecretResolver:
    def resolve(self, reference: str) -> str:
        if not re.fullmatch(r"env:[A-Za-z_][A-Za-z0-9_]*", reference):
            raise OpsMeshError(
                "invalid_request", "Only named environment secret references are supported"
            )
        value = os.environ.get(reference[4:])
        if not value:
            raise OpsMeshError(
                "configuration_missing", "The configured provider credential is unavailable"
            )
        return value
