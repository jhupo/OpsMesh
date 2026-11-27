import abc
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from .message import Message


class Event(abc.ABC, BaseModel):
    """Event 基类。提供获取关键信息的方法，其余信息可直接获取。"""

    model_config = ConfigDict(extra="allow")

    time: datetime = Field(default_factory=lambda: datetime.now(UTC))
    """事件发生时间（UTC）"""

    def get_time(self) -> datetime:
        """获取事件发生时间的方法。"""
        return self.time

    @abc.abstractmethod
    def get_type(self) -> str:
        """获取事件类型的方法。"""
        raise NotImplementedError

    @abc.abstractmethod
    def get_event_name(self) -> str:
        """获取事件名称的方法。"""
        raise NotImplementedError

    @abc.abstractmethod
    def get_event_description(self) -> str:
        """获取事件描述的方法，通常为事件具体内容。"""
        raise NotImplementedError

    def __str__(self) -> str:
        return f"[{self.get_event_name()}]: {self.get_event_description()}"

    def get_log_string(self) -> str:
        """获取事件日志信息的方法。

        通常你不需要修改这个方法，只有当希望 OpsMesh 隐藏该事件日志时，
        可以抛出 `NoLogException` 异常。

        异常:
            NoLogException: 希望 OpsMesh 隐藏该事件日志
        """
        return f"[{self.get_event_name()}]: {self.get_event_description()}"

    @abc.abstractmethod
    def get_user_id(self) -> str:
        """获取事件主体 id 的方法。"""
        raise NotImplementedError

    @abc.abstractmethod
    def get_session_id(self) -> str:
        """获取会话 id 的方法，用于判断当前事件属于哪一个会话。"""
        raise NotImplementedError

    @abc.abstractmethod
    def get_message(self) -> "Message":
        """获取事件消息内容的方法。"""
        raise NotImplementedError

    def get_plaintext(self) -> str:
        """获取消息纯文本的方法。

        通常不需要修改，默认通过 `get_message().extract_plain_text` 获取。
        """
        return self.get_message().extract_plain_text()

    @abc.abstractmethod
    def is_tome(self) -> bool:
        """获取事件是否与当前网关实例有关的方法。"""
        raise NotImplementedError
