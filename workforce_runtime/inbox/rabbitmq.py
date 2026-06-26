from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import pika

from workforce_runtime.core import AgentInboxItem


class RabbitMQAgentInboxQueue:
    """RabbitMQ broker for per-agent inbox delivery.

    The runtime keeps durable item state in its store. RabbitMQ carries a
    persistent copy of each item so dispatchers can claim work by agent queue.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        self.host = str(config.get("host") or "127.0.0.1")
        self.port = int(config.get("port") or 5672)
        self.username = str(config.get("username") or "guest")
        self.password = str(config.get("password") or "guest")
        self.virtual_host = str(config.get("virtual_host") or "/")
        self.exchange = str(config.get("exchange") or "workforce.agent_inbox")
        self.queue_prefix = str(config.get("queue_prefix") or "workforce.agent.")
        self.heartbeat = int(config.get("heartbeat") or 30)
        self.blocked_connection_timeout = int(config.get("blocked_connection_timeout") or 30)

    def publish(self, item: AgentInboxItem) -> None:
        with self._channel() as channel:
            self._declare_exchange(channel)
            queue_name = self._queue_name(item.agent_id)
            routing_key = self._routing_key(item.agent_id)
            self._declare_queue(channel, queue_name, routing_key)
            channel.basic_publish(
                exchange=self.exchange,
                routing_key=routing_key,
                body=item.model_dump_json().encode("utf-8"),
                properties=pika.BasicProperties(
                    content_type="application/json",
                    delivery_mode=2,
                    priority=max(0, min(int(item.priority), 255)),
                    message_id=item.inbox_item_id,
                    type=item.kind,
                ),
                mandatory=True,
            )

    def claim(self, *, agent_id: str, limit: int = 1) -> list[AgentInboxItem]:
        if limit <= 0:
            return []
        claimed: list[AgentInboxItem] = []
        with self._channel() as channel:
            self._declare_exchange(channel)
            queue_name = self._queue_name(agent_id)
            self._declare_queue(channel, queue_name, self._routing_key(agent_id))
            for _ in range(limit):
                method, _properties, body = channel.basic_get(queue=queue_name, auto_ack=False)
                if method is None:
                    break
                try:
                    claimed.append(AgentInboxItem.model_validate_json(body))
                    channel.basic_ack(method.delivery_tag)
                except Exception:
                    channel.basic_nack(method.delivery_tag, requeue=False)
                    raise
        return claimed

    def ensure_agent_queues(self, agent_ids: Iterable[str]) -> None:
        with self._channel() as channel:
            self._declare_exchange(channel)
            for agent_id in agent_ids:
                self._declare_queue(channel, self._queue_name(agent_id), self._routing_key(agent_id))

    def _connection(self) -> pika.BlockingConnection:
        credentials = pika.PlainCredentials(self.username, self.password)
        params = pika.ConnectionParameters(
            host=self.host,
            port=self.port,
            virtual_host=self.virtual_host,
            credentials=credentials,
            heartbeat=self.heartbeat,
            blocked_connection_timeout=self.blocked_connection_timeout,
        )
        return pika.BlockingConnection(params)

    def _channel(self) -> _ChannelContext:
        return _ChannelContext(self._connection())

    def _declare_exchange(self, channel: pika.channel.Channel) -> None:
        channel.exchange_declare(exchange=self.exchange, exchange_type="direct", durable=True)

    def _declare_queue(self, channel: pika.channel.Channel, queue_name: str, routing_key: str) -> None:
        channel.queue_declare(queue=queue_name, durable=True, arguments={"x-max-priority": 255})
        channel.queue_bind(queue=queue_name, exchange=self.exchange, routing_key=routing_key)

    def _queue_name(self, agent_id: str) -> str:
        return f"{self.queue_prefix}{agent_id}"

    @staticmethod
    def _routing_key(agent_id: str) -> str:
        return f"agent.{agent_id}"


class _ChannelContext:
    def __init__(self, connection: pika.BlockingConnection) -> None:
        self.connection = connection
        self.channel: pika.channel.Channel | None = None

    def __enter__(self) -> pika.channel.Channel:
        self.channel = self.connection.channel()
        return self.channel

    def __exit__(self, *_args: object) -> None:
        if self.channel is not None and self.channel.is_open:
            self.channel.close()
        if self.connection.is_open:
            self.connection.close()
