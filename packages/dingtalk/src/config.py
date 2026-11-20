from pydantic import BaseModel, Field, field_validator
from .protocol.config import DingTalkConnectionSettings

class Settings(BaseModel):
    accounts: list[DingTalkConnectionSettings] = Field(default_factory=list)

    @field_validator("accounts")
    @classmethod
    def unique_accounts(cls, accounts):
        if len({a.client_id for a in accounts}) != len(accounts):
            raise ValueError("Duplicate DingTalk client_id")
        return accounts

class Config(BaseModel):
    dingtalk: Settings = Field(default_factory=Settings)
