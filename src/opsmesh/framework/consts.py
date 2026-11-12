"""State keys shared by event dispatch and matcher handlers."""

import os
import sys
from typing import Literal

# used by Matcher
RECEIVE_KEY: Literal["_receive_{id}"] = "_receive_{id}"
"""State key for a received event."""
LAST_RECEIVE_KEY: Literal["_last_receive"] = "_last_receive"
"""State key for the most recently received event."""
ARG_KEY: Literal["{key}"] = "{key}"
"""State key for a collected argument."""
REJECT_TARGET: Literal["_current_target"] = "_current_target"
"""State key for the current rejection target."""
REJECT_CACHE_TARGET: Literal["_next_target"] = "_next_target"
"""State key for the next rejection target."""
PAUSE_PROMPT_RESULT_KEY: Literal["_pause_result"] = "_pause_result"
"""State key for the result of sending a pause prompt."""
REJECT_PROMPT_RESULT_KEY: Literal["_reject_{key}_result"] = (
    "_reject_{key}_result"
)
"""State key for the result of sending a rejection prompt."""

# used by Rule
PREFIX_KEY: Literal["_prefix"] = "_prefix"
"""State key for the parsed command prefix."""

CMD_KEY: Literal["command"] = "command"
"""State key for the command tuple."""
RAW_CMD_KEY: Literal["raw_command"] = "raw_command"
"""State key for the raw command text."""
CMD_ARG_KEY: Literal["command_arg"] = "command_arg"
"""State key for command arguments."""
CMD_START_KEY: Literal["command_start"] = "command_start"
"""State key for the command start marker."""
CMD_WHITESPACE_KEY: Literal["command_whitespace"] = "command_whitespace"
"""State key for whitespace between a command and its arguments."""

SHELL_ARGS: Literal["_args"] = "_args"
"""State key for parsed shell-command arguments."""
SHELL_ARGV: Literal["_argv"] = "_argv"
"""State key for the original shell-command argument list."""

REGEX_MATCHED: Literal["_matched"] = "_matched"
"""State key for the regular-expression match."""
STARTSWITH_KEY: Literal["_startswith"] = "_startswith"
"""State key for the prefix that triggered a matcher."""
ENDSWITH_KEY: Literal["_endswith"] = "_endswith"
"""State key for the suffix that triggered a matcher."""
FULLMATCH_KEY: Literal["_fullmatch"] = "_fullmatch"
"""State key for the full message that triggered a matcher."""
KEYWORD_KEY: Literal["_keyword"] = "_keyword"
"""State key for the keyword that triggered a matcher."""

WINDOWS = sys.platform.startswith("win") or (
    sys.platform == "cli" and os.name == "nt"
)
