"""Pluggable push delivery.

Same interface shape as the SMS backends, for the same reason: the provider
is a deployment decision and the app should not know which one is wired.

What is specific to push in this app: the address is a **device token**, not a
person. One user has several devices and a token dies without telling anyone,
so `send()` reports a dead token distinctly (`invalid_token`) and the caller
clears it off the `Device` row instead of retrying it forever.
"""

from dataclasses import dataclass, field
from importlib import import_module

from django.conf import settings


@dataclass
class PushResult:
    ok: bool
    provider: str = ""
    message_id: str = ""
    error: str = ""
    #: The token is dead: unregister it rather than trying again.
    invalid_token: bool = False
    raw: dict = field(default_factory=dict)


class PushBackend:
    name = "base"

    def __init__(self, **options):
        self.options = options

    def send(self, token: str, title: str, body: str, *, data: dict | None = None) -> PushResult:
        raise NotImplementedError


def get_backend(path: str | None = None, **kwargs) -> PushBackend:
    dotted = path or getattr(
        settings, "PUSH_BACKEND", "apps.communication.push.console.ConsolePushBackend"
    )
    module_path, _, class_name = dotted.rpartition(".")
    backend_class = getattr(import_module(module_path), class_name)
    return backend_class(**kwargs)
