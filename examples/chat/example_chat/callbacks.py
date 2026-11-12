"""Observe model usage without implementing a native RunHooks subclass."""

import logging
from typing import Annotated

from agents.items import ModelResponse

from opsmesh.params import Context

from . import channels, execution

logger = logging.getLogger(__name__)


@execution.on_llm_end
async def log_model_usage(
    response: ModelResponse,
    caller: Annotated[channels.UserContext, Context()],
) -> None:
    """Log token counts without recording model or tool content."""
    logger.info(
        "Caller %s used %d tokens", caller.id, response.usage.total_tokens
    )
