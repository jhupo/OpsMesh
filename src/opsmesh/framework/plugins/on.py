"""Convenience factories for event matcher classes and groups."""

import inspect
import re
import warnings
from datetime import datetime, timedelta
from types import ModuleType
from typing import Any

from opsmesh.framework.adapters import Event
from opsmesh.framework.dependencies import Dependent
from opsmesh.framework.matchers import Matcher, MatcherSource
from opsmesh.framework.permissions import Permission
from opsmesh.framework.rules import (
    ArgumentParser,
    Rule,
    command,
    endswith,
    fullmatch,
    is_type,
    keyword,
    regex,
    shell_command,
    startswith,
)
from opsmesh.framework.typing import (
    T_Handler,
    T_PermissionChecker,
    T_RuleChecker,
    T_State,
)

from .model import Plugin
from .registry import _current_plugin, get_plugin_by_module_name


def store_matcher(matcher: type[Matcher]) -> None:
    """Associate a matcher with the plugin currently being loaded.

    Args:
        matcher: The matcher instance.
    """
    # only store the matcher defined when plugin loading
    if plugin := _current_plugin.get():
        plugin.matcher.add(matcher)


def get_matcher_plugin(depth: int = 1) -> Plugin | None:  # pragma: no cover
    """Return the plugin in which a matcher was defined.

    Deprecated: use get_matcher_source instead.

    Args:
        depth: The number of caller frames to inspect.
    """
    warnings.warn(
        "`get_matcher_plugin` is deprecated, "
        "please use `get_matcher_source` instead",
        DeprecationWarning,
        stacklevel=2,
    )
    return (source := get_matcher_source(depth + 1)) and source.plugin


def get_matcher_module(depth: int = 1) -> ModuleType | None:  # pragma: no cover
    """Return the module in which a matcher was defined.

    Deprecated: use get_matcher_source instead.

    Args:
        depth: The number of caller frames to inspect.
    """
    warnings.warn(
        "`get_matcher_module` is deprecated, "
        "please use `get_matcher_source` instead",
        DeprecationWarning,
        stacklevel=2,
    )
    return (source := get_matcher_source(depth + 1)) and source.module


def get_matcher_source(depth: int = 0) -> MatcherSource | None:
    """Return source metadata for the matcher definition.

    Args:
        depth: The number of caller frames to inspect.
    """
    current_frame = inspect.currentframe()
    if current_frame is None:
        return None

    frame = current_frame
    d = depth + 1
    while d > 0:
        frame = frame.f_back
        if frame is None:
            raise ValueError("Depth out of range")
        d -= 1

    module_name = (module := inspect.getmodule(frame)) and module.__name__

    # matcher defined when plugin loading
    plugin: Plugin | None = _current_plugin.get()
    # matcher defined when plugin running
    if plugin is None and module_name:
        plugin = get_plugin_by_module_name(module_name)

    return MatcherSource(
        plugin_id=plugin and plugin.id_,
        module_name=module_name,
        lineno=frame.f_lineno,
    )


def on(
    type: str = "",
    rule: Rule | T_RuleChecker | None = None,
    permission: Permission | T_PermissionChecker | None = None,
    *,
    handlers: list[T_Handler | Dependent[Any]] | None = None,
    temp: bool = False,
    expire_time: datetime | timedelta | None = None,
    priority: int = 1,
    block: bool = False,
    state: T_State | None = None,
    _depth: int = 0,
) -> type[Matcher]:
    """Register an event matcher with an optional event type.

    Args:
        type: The event type to match.
        rule: The event matching rule.
        permission: The event permission check.
        handlers: The event handler sequence.
        temp: Whether this matcher runs only once.
        expire_time: The deadline after which this matcher is removed on
            checking.
        priority: Dispatch priority; smaller values run first.
        block: Whether to block lower-priority matchers.
        state: The default state mapping.
    """
    matcher = Matcher.new(
        type,
        Rule() & rule,
        Permission() | permission,
        temp=temp,
        expire_time=expire_time,
        priority=priority,
        block=block,
        handlers=handlers,
        source=get_matcher_source(_depth + 1),
        default_state=state,
    )
    store_matcher(matcher)
    return matcher


def on_metaevent(*args, _depth: int = 0, **kwargs) -> type[Matcher]:
    """Register a meta-event matcher.

    Args:
        *args: Positional options forwarded to matcher registration.
        **kwargs: Additional matcher registration options.
        rule: The event matching rule.
        permission: The event permission check.
        handlers: The event handler sequence.
        temp: Whether this matcher runs only once.
        expire_time: The deadline after which this matcher is removed on
            checking.
        priority: Dispatch priority; smaller values run first.
        block: Whether to block lower-priority matchers.
        state: The default state mapping.
    """
    return on("meta_event", *args, **kwargs, _depth=_depth + 1)


def on_message(*args, _depth: int = 0, **kwargs) -> type[Matcher]:
    """Register a message-event matcher.

    Args:
        *args: Positional options forwarded to matcher registration.
        **kwargs: Additional matcher registration options.
        rule: The event matching rule.
        permission: The event permission check.
        handlers: The event handler sequence.
        temp: Whether this matcher runs only once.
        expire_time: The deadline after which this matcher is removed on
            checking.
        priority: Dispatch priority; smaller values run first.
        block: Whether to block lower-priority matchers.
        state: The default state mapping.
    """
    kwargs.setdefault("block", True)
    return on("message", *args, **kwargs, _depth=_depth + 1)


def on_notice(*args, _depth: int = 0, **kwargs) -> type[Matcher]:
    """Register a notice-event matcher.

    Args:
        *args: Positional options forwarded to matcher registration.
        **kwargs: Additional matcher registration options.
        rule: The event matching rule.
        permission: The event permission check.
        handlers: The event handler sequence.
        temp: Whether this matcher runs only once.
        expire_time: The deadline after which this matcher is removed on
            checking.
        priority: Dispatch priority; smaller values run first.
        block: Whether to block lower-priority matchers.
        state: The default state mapping.
    """
    return on("notice", *args, **kwargs, _depth=_depth + 1)


def on_request(*args, _depth: int = 0, **kwargs) -> type[Matcher]:
    """Register a request-event matcher.

    Args:
        *args: Positional options forwarded to matcher registration.
        **kwargs: Additional matcher registration options.
        rule: The event matching rule.
        permission: The event permission check.
        handlers: The event handler sequence.
        temp: Whether this matcher runs only once.
        expire_time: The deadline after which this matcher is removed on
            checking.
        priority: Dispatch priority; smaller values run first.
        block: Whether to block lower-priority matchers.
        state: The default state mapping.
    """
    return on("request", *args, **kwargs, _depth=_depth + 1)


def on_startswith(
    msg: str | tuple[str, ...],
    rule: Rule | T_RuleChecker | None = None,
    ignorecase: bool = False,
    _depth: int = 0,
    **kwargs,
) -> type[Matcher]:
    """Register a message matcher for text starting with the specified content.

    Args:
        **kwargs: Additional matcher registration options.
        msg: The required text prefix.
        rule: The event matching rule.
        ignorecase: Whether matching ignores case.
        permission: The event permission check.
        handlers: The event handler sequence.
        temp: Whether this matcher runs only once.
        expire_time: The deadline after which this matcher is removed on
            checking.
        priority: Dispatch priority; smaller values run first.
        block: Whether to block lower-priority matchers.
        state: The default state mapping.
    """
    return on_message(
        startswith(msg, ignorecase) & rule, **kwargs, _depth=_depth + 1
    )


def on_endswith(
    msg: str | tuple[str, ...],
    rule: Rule | T_RuleChecker | None = None,
    ignorecase: bool = False,
    _depth: int = 0,
    **kwargs,
) -> type[Matcher]:
    """Register a message matcher for text ending with the specified content.

    Args:
        **kwargs: Additional matcher registration options.
        msg: The required text suffix.
        rule: The event matching rule.
        ignorecase: Whether matching ignores case.
        permission: The event permission check.
        handlers: The event handler sequence.
        temp: Whether this matcher runs only once.
        expire_time: The deadline after which this matcher is removed on
            checking.
        priority: Dispatch priority; smaller values run first.
        block: Whether to block lower-priority matchers.
        state: The default state mapping.
    """
    return on_message(
        endswith(msg, ignorecase) & rule, **kwargs, _depth=_depth + 1
    )


def on_fullmatch(
    msg: str | tuple[str, ...],
    rule: Rule | T_RuleChecker | None = None,
    ignorecase: bool = False,
    _depth: int = 0,
    **kwargs,
) -> type[Matcher]:
    """Register a message matcher for an exact text match.

    Args:
        **kwargs: Additional matcher registration options.
        msg: The required complete text.
        rule: The event matching rule.
        ignorecase: Whether matching ignores case.
        permission: The event permission check.
        handlers: The event handler sequence.
        temp: Whether this matcher runs only once.
        expire_time: The deadline after which this matcher is removed on
            checking.
        priority: Dispatch priority; smaller values run first.
        block: Whether to block lower-priority matchers.
        state: The default state mapping.
    """
    return on_message(
        fullmatch(msg, ignorecase) & rule, **kwargs, _depth=_depth + 1
    )


def on_keyword(
    keywords: set[str],
    rule: Rule | T_RuleChecker | None = None,
    _depth: int = 0,
    **kwargs,
) -> type[Matcher]:
    """Register a message matcher for keywords in the plain-text content.

    Args:
        **kwargs: Additional matcher registration options.
        keywords: The keywords to match.
        rule: The event matching rule.
        permission: The event permission check.
        handlers: The event handler sequence.
        temp: Whether this matcher runs only once.
        expire_time: The deadline after which this matcher is removed on
            checking.
        priority: Dispatch priority; smaller values run first.
        block: Whether to block lower-priority matchers.
        state: The default state mapping.
    """
    return on_message(keyword(*keywords) & rule, **kwargs, _depth=_depth + 1)


def on_command(
    cmd: str | tuple[str, ...],
    rule: Rule | T_RuleChecker | None = None,
    aliases: set[str | tuple[str, ...]] | None = None,
    force_whitespace: str | bool | None = None,
    _depth: int = 0,
    **kwargs,
) -> type[Matcher]:
    """Register a message matcher for a command prefix.

    See opsmesh.framework.rules.command for command matching behavior.

    Args:
        **kwargs: Additional matcher registration options.
        cmd: The command name or tuple.
        rule: The event matching rule.
        aliases: Alternative command names.
        force_whitespace: Whether specific whitespace must follow the
            command.
        permission: The event permission check.
        handlers: The event handler sequence.
        temp: Whether this matcher runs only once.
        expire_time: The deadline after which this matcher is removed on
            checking.
        priority: Dispatch priority; smaller values run first.
        block: Whether to block lower-priority matchers.
        state: The default state mapping.
    """
    commands = {cmd} | (aliases or set())
    kwargs.setdefault("block", False)
    return on_message(
        command(*commands, force_whitespace=force_whitespace) & rule,
        **kwargs,
        _depth=_depth + 1,
    )


def on_shell_command(
    cmd: str | tuple[str, ...],
    rule: Rule | T_RuleChecker | None = None,
    aliases: set[str | tuple[str, ...]] | None = None,
    parser: ArgumentParser | None = None,
    _depth: int = 0,
    **kwargs,
) -> type[Matcher]:
    """Register a command matcher supporting shell-style argument parsing.

    Supplying parser enables argument parsing in addition to command
    matching.

    Use ShellCommandArgv to retrieve the original argument list
    and ShellCommandArgs to retrieve parsed arguments.

    Args:
        **kwargs: Additional matcher registration options.
        cmd: The command name or tuple.
        rule: The event matching rule.
        aliases: Alternative command names.
        parser: An ArgumentParser instance.
        permission: The event permission check.
        handlers: The event handler sequence.
        temp: Whether this matcher runs only once.
        expire_time: The deadline after which this matcher is removed on
            checking.
        priority: Dispatch priority; smaller values run first.
        block: Whether to block lower-priority matchers.
        state: The default state mapping.
    """
    commands = {cmd} | (aliases or set())
    return on_message(
        shell_command(*commands, parser=parser) & rule,
        **kwargs,
        _depth=_depth + 1,
    )


def on_regex(
    pattern: str,
    flags: int | re.RegexFlag = 0,
    rule: Rule | T_RuleChecker | None = None,
    _depth: int = 0,
    **kwargs,
) -> type[Matcher]:
    """Register a message matcher for a regular expression.

    See opsmesh.framework.rules.regex for matching behavior.

    Args:
        **kwargs: Additional matcher registration options.
        pattern: The regular-expression pattern.
        flags: Regular-expression flags.
        rule: The event matching rule.
        permission: The event permission check.
        handlers: The event handler sequence.
        temp: Whether this matcher runs only once.
        expire_time: The deadline after which this matcher is removed on
            checking.
        priority: Dispatch priority; smaller values run first.
        block: Whether to block lower-priority matchers.
        state: The default state mapping.
    """
    return on_message(regex(pattern, flags) & rule, **kwargs, _depth=_depth + 1)


def on_type(
    types: type[Event] | tuple[type[Event], ...],
    rule: Rule | T_RuleChecker | None = None,
    *,
    _depth: int = 0,
    **kwargs,
) -> type[Matcher]:
    """Register a matcher for instances of selected event classes.

    Args:
        **kwargs: Additional matcher registration options.
        types: The event classes to accept.
        rule: The event matching rule.
        permission: The event permission check.
        handlers: The event handler sequence.
        temp: Whether this matcher runs only once.
        expire_time: The deadline after which this matcher is removed on
            checking.
        priority: Dispatch priority; smaller values run first.
        block: Whether to block lower-priority matchers.
        state: The default state mapping.
    """
    event_types = types if isinstance(types, tuple) else (types,)
    return on(rule=is_type(*event_types) & rule, **kwargs, _depth=_depth + 1)


class _Group:
    def __init__(self, **kwargs):
        """Create a matcher group with the same default options as on."""
        self.matchers: list[type[Matcher]] = []
        """Matcher classes registered in this group."""
        self.base_kwargs: dict[str, Any] = kwargs
        """Default keyword arguments forwarded to on."""

    def _get_final_kwargs(
        self, update: dict[str, Any], *, exclude: set[str] | None = None
    ) -> dict[str, Any]:
        """Merge default and supplied matcher options.

        Args:
            update: Keyword arguments overriding the group defaults.
            exclude: Option names to exclude.
        """
        final_kwargs = self.base_kwargs.copy()
        final_kwargs.update(update)
        if exclude:
            for key in exclude:
                final_kwargs.pop(key, None)
        final_kwargs["_depth"] = 1
        return final_kwargs


class CommandGroup(_Group):
    """A group of commands sharing a common name prefix.

    Args:
        cmd: The command name or tuple.
        prefix_aliases: Whether to apply the group prefix to aliases as well.
        rule: The event matching rule.
        permission: The event permission check.
        handlers: The event handler sequence.
        temp: Whether this matcher runs only once.
        expire_time: The deadline after which this matcher is removed on
            checking.
        priority: Dispatch priority; smaller values run first.
        block: Whether to block lower-priority matchers.
        state: The default state mapping.
    """

    def __init__(
        self, cmd: str | tuple[str, ...], prefix_aliases: bool = False, **kwargs
    ):
        """The shared command prefix."""
        super().__init__(**kwargs)
        self.basecmd: tuple[str, ...] = (cmd,) if isinstance(cmd, str) else cmd
        self.base_kwargs.pop("aliases", None)
        self.prefix_aliases = prefix_aliases

    def __repr__(self) -> str:
        return (
            f"CommandGroup(cmd={self.basecmd}, matchers={len(self.matchers)})"
        )

    def command(self, cmd: str | tuple[str, ...], **kwargs) -> type[Matcher]:
        """Register a command, overriding group defaults with supplied options.

        Args:
            **kwargs: Additional matcher registration options.
            cmd: The command name or tuple.
            aliases: Alternative command names.
            force_whitespace: Whether specific whitespace must follow the
                command.
            rule: The event matching rule.
            permission: The event permission check.
            handlers: The event handler sequence.
            temp: Whether this matcher runs only once.
            expire_time: The deadline after which this matcher is removed on
                checking.
            priority: Dispatch priority; smaller values run first.
            block: Whether to block lower-priority matchers.
            state: The default state mapping.
        """
        sub_cmd = (cmd,) if isinstance(cmd, str) else cmd
        cmd = self.basecmd + sub_cmd
        if self.prefix_aliases and (aliases := kwargs.get("aliases")):
            kwargs["aliases"] = {
                self.basecmd + ((alias,) if isinstance(alias, str) else alias)
                for alias in aliases
            }
        matcher = on_command(cmd, **self._get_final_kwargs(kwargs))
        self.matchers.append(matcher)
        return matcher

    def shell_command(
        self, cmd: str | tuple[str, ...], **kwargs
    ) -> type[Matcher]:
        """Register a shell-style command, overriding group defaults as needed.

        Args:
            **kwargs: Additional matcher registration options.
            cmd: The command name or tuple.
            rule: The event matching rule.
            aliases: Alternative command names.
            parser: An ArgumentParser instance.
            permission: The event permission check.
            handlers: The event handler sequence.
            temp: Whether this matcher runs only once.
            expire_time: The deadline after which this matcher is removed on
                checking.
            priority: Dispatch priority; smaller values run first.
            block: Whether to block lower-priority matchers.
            state: The default state mapping.
        """
        sub_cmd = (cmd,) if isinstance(cmd, str) else cmd
        cmd = self.basecmd + sub_cmd
        if self.prefix_aliases and (aliases := kwargs.get("aliases")):
            kwargs["aliases"] = {
                self.basecmd + ((alias,) if isinstance(alias, str) else alias)
                for alias in aliases
            }
        matcher = on_shell_command(cmd, **self._get_final_kwargs(kwargs))
        self.matchers.append(matcher)
        return matcher


class MatcherGroup(_Group):
    """A collection of matchers created with shared default options."""

    def __repr__(self) -> str:
        return f"MatcherGroup(matchers={len(self.matchers)})"

    def on(self, **kwargs) -> type[Matcher]:
        """Register an event matcher with an optional event type.

        Args:
            **kwargs: Additional matcher registration options.
            type: The event type to match.
            rule: The event matching rule.
            permission: The event permission check.
            handlers: The event handler sequence.
            temp: Whether this matcher runs only once.
            expire_time: The deadline after which this matcher is removed on
                checking.
            priority: Dispatch priority; smaller values run first.
            block: Whether to block lower-priority matchers.
            state: The default state mapping.
        """
        matcher = on(**self._get_final_kwargs(kwargs))
        self.matchers.append(matcher)
        return matcher

    def on_metaevent(self, **kwargs) -> type[Matcher]:
        """Register a meta-event matcher.

        Args:
            **kwargs: Additional matcher registration options.
            rule: The event matching rule.
            permission: The event permission check.
            handlers: The event handler sequence.
            temp: Whether this matcher runs only once.
            expire_time: The deadline after which this matcher is removed on
                checking.
            priority: Dispatch priority; smaller values run first.
            block: Whether to block lower-priority matchers.
            state: The default state mapping.
        """
        final_kwargs = self._get_final_kwargs(
            kwargs, exclude={"type", "permission"}
        )
        matcher = on_metaevent(**final_kwargs)
        self.matchers.append(matcher)
        return matcher

    def on_message(self, **kwargs) -> type[Matcher]:
        """Register a message-event matcher.

        Args:
            **kwargs: Additional matcher registration options.
            rule: The event matching rule.
            permission: The event permission check.
            handlers: The event handler sequence.
            temp: Whether this matcher runs only once.
            expire_time: The deadline after which this matcher is removed on
                checking.
            priority: Dispatch priority; smaller values run first.
            block: Whether to block lower-priority matchers.
            state: The default state mapping.
        """
        final_kwargs = self._get_final_kwargs(kwargs, exclude={"type"})
        matcher = on_message(**final_kwargs)
        self.matchers.append(matcher)
        return matcher

    def on_notice(self, **kwargs) -> type[Matcher]:
        """Register a notice-event matcher.

        Args:
            **kwargs: Additional matcher registration options.
            rule: The event matching rule.
            permission: The event permission check.
            handlers: The event handler sequence.
            temp: Whether this matcher runs only once.
            expire_time: The deadline after which this matcher is removed on
                checking.
            priority: Dispatch priority; smaller values run first.
            block: Whether to block lower-priority matchers.
            state: The default state mapping.
        """
        final_kwargs = self._get_final_kwargs(
            kwargs, exclude={"type", "permission"}
        )
        matcher = on_notice(**final_kwargs)
        self.matchers.append(matcher)
        return matcher

    def on_request(self, **kwargs) -> type[Matcher]:
        """Register a request-event matcher.

        Args:
            **kwargs: Additional matcher registration options.
            rule: The event matching rule.
            permission: The event permission check.
            handlers: The event handler sequence.
            temp: Whether this matcher runs only once.
            expire_time: The deadline after which this matcher is removed on
                checking.
            priority: Dispatch priority; smaller values run first.
            block: Whether to block lower-priority matchers.
            state: The default state mapping.
        """
        final_kwargs = self._get_final_kwargs(
            kwargs, exclude={"type", "permission"}
        )
        matcher = on_request(**final_kwargs)
        self.matchers.append(matcher)
        return matcher

    def on_startswith(
        self, msg: str | tuple[str, ...], **kwargs
    ) -> type[Matcher]:
        """Register a matcher for messages with the specified prefix.

        Args:
            **kwargs: Additional matcher registration options.
            msg: The required text prefix.
            ignorecase: Whether matching ignores case.
            rule: The event matching rule.
            permission: The event permission check.
            handlers: The event handler sequence.
            temp: Whether this matcher runs only once.
            expire_time: The deadline after which this matcher is removed on
                checking.
            priority: Dispatch priority; smaller values run first.
            block: Whether to block lower-priority matchers.
            state: The default state mapping.
        """
        final_kwargs = self._get_final_kwargs(kwargs, exclude={"type"})
        matcher = on_startswith(msg, **final_kwargs)
        self.matchers.append(matcher)
        return matcher

    def on_endswith(
        self, msg: str | tuple[str, ...], **kwargs
    ) -> type[Matcher]:
        """Register a matcher for messages with the specified suffix.

        Args:
            **kwargs: Additional matcher registration options.
            msg: The required text suffix.
            ignorecase: Whether matching ignores case.
            rule: The event matching rule.
            permission: The event permission check.
            handlers: The event handler sequence.
            temp: Whether this matcher runs only once.
            expire_time: The deadline after which this matcher is removed on
                checking.
            priority: Dispatch priority; smaller values run first.
            block: Whether to block lower-priority matchers.
            state: The default state mapping.
        """
        final_kwargs = self._get_final_kwargs(kwargs, exclude={"type"})
        matcher = on_endswith(msg, **final_kwargs)
        self.matchers.append(matcher)
        return matcher

    def on_fullmatch(
        self, msg: str | tuple[str, ...], **kwargs
    ) -> type[Matcher]:
        """Register a message matcher for an exact text match.

        Args:
            **kwargs: Additional matcher registration options.
            msg: The required complete text.
            rule: The event matching rule.
            ignorecase: Whether matching ignores case.
            permission: The event permission check.
            handlers: The event handler sequence.
            temp: Whether this matcher runs only once.
            expire_time: The deadline after which this matcher is removed on
                checking.
            priority: Dispatch priority; smaller values run first.
            block: Whether to block lower-priority matchers.
            state: The default state mapping.
        """
        final_kwargs = self._get_final_kwargs(kwargs, exclude={"type"})
        matcher = on_fullmatch(msg, **final_kwargs)
        self.matchers.append(matcher)
        return matcher

    def on_keyword(self, keywords: set[str], **kwargs) -> type[Matcher]:
        """Register a message matcher for keywords in the plain-text content.

        Args:
            **kwargs: Additional matcher registration options.
            keywords: The keywords to match.
            rule: The event matching rule.
            permission: The event permission check.
            handlers: The event handler sequence.
            temp: Whether this matcher runs only once.
            expire_time: The deadline after which this matcher is removed on
                checking.
            priority: Dispatch priority; smaller values run first.
            block: Whether to block lower-priority matchers.
            state: The default state mapping.
        """
        final_kwargs = self._get_final_kwargs(kwargs, exclude={"type"})
        matcher = on_keyword(keywords, **final_kwargs)
        self.matchers.append(matcher)
        return matcher

    def on_command(
        self,
        cmd: str | tuple[str, ...],
        aliases: set[str | tuple[str, ...]] | None = None,
        force_whitespace: str | bool | None = None,
        **kwargs,
    ) -> type[Matcher]:
        """Register a message matcher for a command prefix.

        See opsmesh.framework.rules.command for command matching behavior.

        Args:
            **kwargs: Additional matcher registration options.
            cmd: The command name or tuple.
            aliases: Alternative command names.
            force_whitespace: Whether specific whitespace must follow the
                command.
            rule: The event matching rule.
            permission: The event permission check.
            handlers: The event handler sequence.
            temp: Whether this matcher runs only once.
            expire_time: The deadline after which this matcher is removed on
                checking.
            priority: Dispatch priority; smaller values run first.
            block: Whether to block lower-priority matchers.
            state: The default state mapping.
        """
        final_kwargs = self._get_final_kwargs(kwargs, exclude={"type"})
        matcher = on_command(
            cmd,
            aliases=aliases,
            force_whitespace=force_whitespace,
            **final_kwargs,
        )
        self.matchers.append(matcher)
        return matcher

    def on_shell_command(
        self,
        cmd: str | tuple[str, ...],
        aliases: set[str | tuple[str, ...]] | None = None,
        parser: ArgumentParser | None = None,
        **kwargs,
    ) -> type[Matcher]:
        """Register a command matcher supporting shell-style argument parsing.

        Supplying parser enables argument parsing in addition to command
        matching.

        Use ShellCommandArgv to retrieve the original argument list
        and ShellCommandArgs to retrieve parsed arguments.

        Args:
            **kwargs: Additional matcher registration options.
            cmd: The command name or tuple.
            aliases: Alternative command names.
            parser: An ArgumentParser instance.
            rule: The event matching rule.
            permission: The event permission check.
            handlers: The event handler sequence.
            temp: Whether this matcher runs only once.
            expire_time: The deadline after which this matcher is removed on
                checking.
            priority: Dispatch priority; smaller values run first.
            block: Whether to block lower-priority matchers.
            state: The default state mapping.
        """
        final_kwargs = self._get_final_kwargs(kwargs, exclude={"type"})
        matcher = on_shell_command(
            cmd, aliases=aliases, parser=parser, **final_kwargs
        )
        self.matchers.append(matcher)
        return matcher

    def on_regex(
        self, pattern: str, flags: int | re.RegexFlag = 0, **kwargs
    ) -> type[Matcher]:
        """Register a message matcher for a regular expression.

        See opsmesh.framework.rules.regex for matching behavior.

        Args:
            **kwargs: Additional matcher registration options.
            pattern: The regular-expression pattern.
            flags: Regular-expression flags.
            rule: The event matching rule.
            permission: The event permission check.
            handlers: The event handler sequence.
            temp: Whether this matcher runs only once.
            expire_time: The deadline after which this matcher is removed on
                checking.
            priority: Dispatch priority; smaller values run first.
            block: Whether to block lower-priority matchers.
            state: The default state mapping.
        """
        final_kwargs = self._get_final_kwargs(kwargs, exclude={"type"})
        matcher = on_regex(pattern, flags=flags, **final_kwargs)
        self.matchers.append(matcher)
        return matcher

    def on_type(
        self, types: type[Event] | tuple[type[Event]], **kwargs
    ) -> type[Matcher]:
        """Register a matcher for instances of selected event classes.

        Args:
            **kwargs: Additional matcher registration options.
            types: The event classes to accept.
            rule: The event matching rule.
            permission: The event permission check.
            handlers: The event handler sequence.
            temp: Whether this matcher runs only once.
            expire_time: The deadline after which this matcher is removed on
                checking.
            priority: Dispatch priority; smaller values run first.
            block: Whether to block lower-priority matchers.
            state: The default state mapping.
        """
        final_kwargs = self._get_final_kwargs(kwargs, exclude={"type"})
        matcher = on_type(types, **final_kwargs)
        self.matchers.append(matcher)
        return matcher
