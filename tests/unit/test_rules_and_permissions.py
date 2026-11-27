"""Rules, TrieRule command/shell parsing, and Permission combinator tests."""

import pytest

import opsmesh
from opsmesh.adapters import Adapter, Event, Gateway, Message, MessageSegment
from opsmesh.config import Config, Env
from opsmesh.drivers.none import Driver as NoneDriver
from opsmesh.exception import ParserExit
from opsmesh.permission import (
    MESSAGE,
    METAEVENT,
    NOTICE,
    REQUEST,
    SUPERUSER,
    USER,
    Permission,
)
from opsmesh.rule import (
    ArgumentParser,
    TrieRule,
    command,
    endswith,
    fullmatch,
    is_type,
    keyword,
    regex,
    shell_command,
    startswith,
    to_me,
)


class _Seg(MessageSegment["_Msg"]):
    @classmethod
    def get_message_class(cls):
        return _Msg

    def __str__(self) -> str:
        return self.data["text"]

    def is_text(self) -> bool:
        return True


class _Msg(Message[_Seg]):
    @classmethod
    def get_segment_class(cls):
        return _Seg

    @staticmethod
    def _construct(msg: str):
        yield _Seg("text", {"text": msg})


class _Adapter(Adapter):
    @classmethod
    def get_name(cls) -> str:
        return "ops"

    async def _invoke(self, gateway: Gateway, action: str, **params):
        return None


class _Gateway(Gateway):
    async def send(self, event, message, **kwargs):
        return None


class _Event(Event):
    kind: str = "message"
    text: str = ""
    uid: str = "u1"
    sid: str = "s1"
    direct: bool = False

    def get_type(self) -> str:
        return self.kind

    def get_event_name(self) -> str:
        return f"ops.{self.kind}"

    def get_event_description(self) -> str:
        return self.text

    def get_user_id(self) -> str:
        return self.uid

    def get_session_id(self) -> str:
        return self.sid

    def get_message(self) -> _Msg:
        return _Msg(self.text)

    def is_tome(self) -> bool:
        return self.direct


class _OtherEvent(_Event):
    pass


def _make_gw(**cfg_kwargs) -> tuple[NoneDriver, _Gateway]:
    driver = NoneDriver(Env(), Config(**cfg_kwargs))
    return driver, _Gateway(_Adapter(driver), "ops:1")


@pytest.mark.anyio
async def test_text_regex_tome_and_is_type_rules():
    _, gw = _make_gw()
    ev = _Event(text="Deploy v2.1 DONE", direct=True)
    state: dict = {}

    assert await startswith("deploy", ignorecase=True)(gw, ev, state) is True
    assert await endswith("done", ignorecase=True)(gw, ev, state) is True
    assert await fullmatch("deploy v2.1 done", ignorecase=True)(gw, ev, state) is True
    assert await keyword("v2.1")(gw, ev, state) is True
    assert await regex(r"v(?P<ver>\d+\.\d+)")(gw, ev, state) is True
    assert await to_me()(gw, ev, state) is True
    assert await is_type(_Event)(gw, ev, state) is True
    assert await is_type(_OtherEvent)(gw, ev, state) is False

    # Rule supports & only; | raises RuntimeError
    combined = startswith("Deploy") & keyword("DONE")
    assert await combined(gw, ev, {}) is True
    with pytest.raises(RuntimeError, match="Or operation between rules"):
        _ = startswith("a") | endswith("b")


@pytest.mark.anyio
async def test_command_and_shell_command_rules(monkeypatch):
    driver, gw = _make_gw(command_start={"/"}, command_sep={"."})
    monkeypatch.setattr(opsmesh, "_driver", driver)

    cmd_rule = command(("svc", "restart"), force_whitespace=True)
    parser = ArgumentParser("run")
    parser.add_argument("-f", "--force", action="store_true")
    parser.add_argument("target")
    sh_rule = shell_command("run", parser=parser)

    try:
        ev_ok = _Event(text="/svc.restart api")
        st_ok: dict = {}
        TrieRule.get_value(gw, ev_ok, st_ok)
        assert await cmd_rule(gw, ev_ok, st_ok) is True

        ev_no_ws = _Event(text="/svc.restartapi")
        st_no_ws: dict = {}
        TrieRule.get_value(gw, ev_no_ws, st_no_ws)
        assert await cmd_rule(gw, ev_no_ws, st_no_ws) is False

        ev_sh = _Event(text="/run --force worker")
        st_sh: dict = {}
        TrieRule.get_value(gw, ev_sh, st_sh)
        assert await sh_rule(gw, ev_sh, st_sh) is True
        assert st_sh["_args"].force is True
        assert st_sh["_args"].target == "worker"

        ev_bad = _Event(text="/run --unknown")
        st_bad: dict = {}
        TrieRule.get_value(gw, ev_bad, st_bad)
        assert await sh_rule(gw, ev_bad, st_bad) is True
        assert isinstance(st_bad["_args"], ParserExit)
    finally:
        TrieRule.prefix.pop("/svc.restart", None)
        TrieRule.prefix.pop("/run", None)


@pytest.mark.anyio
async def test_permissions_and_combinators():
    _, gw = _make_gw(superusers={"ops:admin"})

    assert await MESSAGE(gw, _Event(kind="message")) is True
    assert await NOTICE(gw, _Event(kind="notice")) is True
    assert await REQUEST(gw, _Event(kind="request")) is True
    assert await METAEVENT(gw, _Event(kind="meta_event")) is True
    assert await USER("s1")(gw, _Event(sid="s1")) is True
    assert await USER("s1")(gw, _Event(sid="s2")) is False
    assert await SUPERUSER(gw, _Event(uid="admin")) is True
    assert await SUPERUSER(gw, _Event(uid="guest")) is False

    # Permission supports | only; & raises RuntimeError
    either = SUPERUSER | USER("s9")
    assert await either(gw, _Event(uid="guest", sid="s9")) is True
    assert await either(gw, _Event(uid="guest", sid="s2")) is False
    with pytest.raises(RuntimeError, match="And operation between Permissions"):
        _ = Permission() & SUPERUSER
