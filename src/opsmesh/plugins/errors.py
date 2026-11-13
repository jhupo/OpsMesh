"""Plugin loading errors that callers must not treat as optional failures."""


class PluginLoadDenied(RuntimeError):
    """The host admission check rejected a plugin load."""


class PluginReloadError(RuntimeError):
    """A revoked plugin source was loaded again in the same process."""


class PluginProtectedError(RuntimeError):
    """A management action would stop or remove a protected plugin."""
