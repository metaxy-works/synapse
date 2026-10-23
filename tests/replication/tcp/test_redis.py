# This file is licensed under the Affero General Public License (AGPL) version 3.
# See <https://www.gnu.org/licenses/agpl-3.0.html>.

from unittest.mock import Mock

from parameterized import parameterized

from twisted.internet.defer import CancelledError, ensureDeferred
from twisted.internet.testing import StringTransport

from synapse.logging.context import SENTINEL_CONTEXT, LoggingContext, current_context

from tests.unittest import TestCase

try:
    from txredisapi import RedisFactory, RedisProtocol, ResponseError
except ModuleNotFoundError as exc:
    if exc.name != "txredisapi":
        raise
    HAS_REDIS = False
else:
    from synapse.replication.tcp.redis import RedisSubscriber

    HAS_REDIS = True


class RedisSubscriptionTestCase(TestCase):
    if not HAS_REDIS:
        skip = "Requires txredisapi"

    @parameterized.expand(
        [
            (ready, outcome)
            for ready in (False, True)
            for outcome in ("success", "error", "cancel")
        ]
    )
    def test_subscribe_before_outbound_connection(
        self, outbound_ready: bool, outcome: str
    ) -> None:
        """REPLICATE waits for the outbound pool without losing its log context."""
        factory = RedisFactory(uuid="test", dbid=None, poolsize=1, isLazy=True)
        outbound = RedisProtocol()
        outbound.connected = 1
        outbound_transport = StringTransport()
        outbound.transport = outbound_transport
        if outbound_ready:
            factory.connectionQueue.put(outbound)

        subscriber = RedisSubscriber()
        subscriber.connected = 1
        subscriber.transport = StringTransport()
        subscriber.server_name = "test"
        subscriber.synapse_stream_prefix = "test"
        subscriber.synapse_channel_names = []
        subscriber.synapse_handler = Mock()
        subscriber.synapse_outbound_redis_connection = factory.handler

        async def subscribe() -> None:
            with LoggingContext(name="subscribe-test", server_name="test") as context:
                try:
                    await subscriber._send_subscribe()
                finally:
                    self.assertIs(current_context(), context)

        result = ensureDeferred(subscribe())
        self.assertNoResult(result)
        self.assertIs(current_context(), SENTINEL_CONTEXT)
        subscriber.synapse_handler.new_connection.assert_not_called()
        self.assertEqual(outbound_transport.value(), b"")

        # The subscriber connection becomes ready before the outbound pool.
        subscriber.dataReceived(b"*3\r\n$9\r\nsubscribe\r\n$4\r\ntest\r\n:1\r\n")
        self.assertNoResult(result)
        subscriber.synapse_handler.send_positions_to_connection.assert_not_called()
        if outcome == "cancel":
            result.cancel()
            self.failureResultOf(result, CancelledError)
            subscriber.synapse_handler.send_positions_to_connection.assert_not_called()
            if not outbound_ready:
                factory.connectionQueue.put(outbound)
                self.assertEqual(outbound_transport.value(), b"")
            self.assertIs(current_context(), SENTINEL_CONTEXT)
            return

        if not outbound_ready:
            self.assertEqual(outbound_transport.value(), b"")
            factory.connectionQueue.put(outbound)

        self.assertEqual(
            outbound_transport.value(),
            b"*3\r\n$7\r\nPUBLISH\r\n$4\r\ntest\r\n$10\r\nREPLICATE \r\n",
        )
        self.assertNoResult(result)
        self.assertIs(current_context(), SENTINEL_CONTEXT)
        if outcome == "error":
            outbound.dataReceived(b"-ERR test publish failed\r\n")
            self.failureResultOf(result, ResponseError)
            subscriber.synapse_handler.send_positions_to_connection.assert_not_called()
        else:
            outbound.dataReceived(b":1\r\n")
            self.successResultOf(result)
            subscriber.synapse_handler.new_connection.assert_called_once_with(
                subscriber
            )
            subscriber.synapse_handler.send_positions_to_connection.assert_called_once_with()
        self.assertIs(current_context(), SENTINEL_CONTEXT)
