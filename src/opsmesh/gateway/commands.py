"""Commands are exact messages, never directives parsed out of quoted content."""

from opsmesh_sdk.channels import InboundMessage


def command(message: InboundMessage) -> str | None:
    if len(message.content) != 1 or message.content[0].type != "text":
        return None
    value = message.content[0].text.strip().lower()
    return {
        "/help": "help",
        "/new": "new",
        "/reset": "reset",
        "/cancel": "cancel",
        "/stop": "cancel",
    }.get(value)
