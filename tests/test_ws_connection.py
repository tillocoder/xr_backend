from unittest import TestCase

from app.ws.connection import ManagedWebSocketConnection, OutboundMessage


class _FakeWebSocket:
    client_state = None
    application_state = None


class ManagedWebSocketConnectionTests(TestCase):
    def test_enqueue_replaces_coalesced_message_in_pending_queue(self) -> None:
        connection = ManagedWebSocketConnection(
            ws=_FakeWebSocket(),
            user_id="user-1",
            max_pending_messages=8,
            send_timeout_seconds=5.0,
        )

        first = connection.enqueue(
            OutboundMessage(
                body='{"type":"chat.presence","value":1}',
                event_type="chat.presence",
                coalesce_key="chat.presence:presence:user-1",
            )
        )
        second = connection.enqueue(
            OutboundMessage(
                body='{"type":"chat.presence","value":2}',
                event_type="chat.presence",
                coalesce_key="chat.presence:presence:user-1",
            )
        )

        self.assertEqual(first, "queued")
        self.assertEqual(second, "queued")
        self.assertEqual(connection.pending_messages, 1)
        queued_items = list(connection._queue._queue)
        self.assertEqual(queued_items[0].body, '{"type":"chat.presence","value":2}')
