"""Messaging gateway for Chrysalis."""

from chrysalis.gateway.events import MessageEvent, SendResult, SessionSource

__all__ = ["GatewayService", "MessageEvent", "SendResult", "SessionSource"]


def __getattr__(name: str):
    if name == "GatewayService":
        from chrysalis.gateway.service import GatewayService

        return GatewayService
    raise AttributeError(name)
