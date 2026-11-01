import socket

import pytest
from pytest_socket import SocketConnectBlockedError


# pytest-socket also emits a UserWarning when it blocks; that is expected here.
@pytest.mark.filterwarnings("ignore::UserWarning")
def test_outbound_connections_are_blocked_in_tests():
    # A public IP, so no DNS lookup happens before the guard trips.
    with pytest.raises(SocketConnectBlockedError):
        socket.create_connection(("1.1.1.1", 443), timeout=1)
