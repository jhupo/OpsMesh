"""Use the official integration's SDK-aware payload serialization."""

from temporalio.converter import DataConverter
from temporalio.openai_agents import OpenAIPayloadConverter

sdk_data_converter = DataConverter(payload_converter_class=OpenAIPayloadConverter)
