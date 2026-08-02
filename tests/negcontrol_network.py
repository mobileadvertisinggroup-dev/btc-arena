"""Negative control (run separately): proves the network block catches attempts.

    python3 -m pytest tests/negcontrol_network.py -q
"""
import pytest


def test_deliberate_network_attempt_is_caught():
    import urllib.request
    with pytest.raises(RuntimeError, match="NETWORK BLOCKED"):
        urllib.request.urlopen("http://example.com")


def test_deliberate_socket_attempt_is_caught():
    import socket
    with pytest.raises(RuntimeError, match="NETWORK BLOCKED"):
        socket.socket()


def test_deliberate_curl_subprocess_is_caught():
    import subprocess
    with pytest.raises(RuntimeError, match="NETWORK BLOCKED"):
        subprocess.Popen(["curl", "http://example.com"])
