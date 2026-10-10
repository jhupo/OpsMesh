"""Parse supported POSIX shell wrappers; never match an opaque script as a simple argv."""

import shlex


def command_invocations(command: list[str]) -> list[list[str]] | None:
    if not command:
        return None
    executable = command[0].replace("\\", "/").rsplit("/", 1)[-1]
    # Interpreter names describe syntax, not approval policy or command risk.
    if executable not in {"sh", "bash", "zsh", "dash"}:
        return [command]
    if len(command) != 3 or command[1] not in {"-c", "-lc"}:
        return None
    script = command[2]
    if any(char in script for char in "$`*?[]{}()\n<>"):
        return None
    try:
        lexer = shlex.shlex(script, posix=True, punctuation_chars=";&|")
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = list(lexer)
    except ValueError:
        return None
    commands: list[list[str]] = []
    current: list[str] = []
    for token in tokens:
        if token in {";", "&&", "||", "|"}:
            if not current:
                return None
            commands.append(current)
            current = []
        elif token in {"&", ";;", "|&", "&|"}:
            return None
        else:
            current.append(token)
    if current:
        commands.append(current)
    if not commands or any("=" in item[0] for item in commands):
        return None
    result = [command]
    for item in commands:
        nested = command_invocations(item)
        if nested is None:
            return None
        result.extend(nested)
    return result
