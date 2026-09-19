"""Local applications are separated by effective port at every scope boundary."""

import pytest

from ctrl.browser_scope import host_allowed, normalized_host, scope_host


@pytest.mark.parametrize("url,key", [
    ("http://localhost:8877/", "localhost:8877"),
    ("http://localhost./", "localhost:80"),
    ("https://localhost/", "localhost:443"),
    ("http://127.0.0.1:8877/", "127.0.0.1:8877"),
    ("http://127.10.20.30:8877/", "127.10.20.30:8877"),
    ("http://[0:0:0:0:0:0:0:1]:8877/", "[::1]:8877"),
    ("http://test.localhost:8877/", "test.localhost:8877"),
    ("http://[::ffff:127.0.0.1]:8877/", "[::ffff:7f00:1]:8877"),
    ("https://www.example.org:8877/", "example.org"),
])
def test_scope_keys_include_effective_port_only_for_local_apps(url, key):
    assert scope_host(url) == key


@pytest.mark.parametrize("url,allowed", [
    ("http://127.0.0.1:8877/read", True),
    ("http://127.0.0.1:8878/", False),
    ("http://127.0.0.1:8766/", False),
    ("http://127.0.0.2:8877/", False),
    ("http://localhost:8877/", False),
])
def test_local_scope_is_exact_host_and_port(url, allowed):
    assert host_allowed(url, ["127.0.0.1:8877"]) is allowed


def test_default_ports_and_loopback_ipv6_are_exact():
    assert host_allowed("http://localhost/", ["localhost:80"])
    assert not host_allowed("https://localhost/", ["localhost:80"])
    assert host_allowed("https://localhost/", ["localhost:443"])
    assert host_allowed("http://[::1]:8877/", ["[::1]:8877"])
    assert not host_allowed("http://[::1]:8878/", ["[::1]:8877"])
    assert not host_allowed("http://localhost:8877/", ["localhost"])
    assert not host_allowed("http://other.localhost:8877/", ["localhost:8877"])
    assert host_allowed("http://[::ffff:127.0.0.1]:8877/", ["[::ffff:7f00:1]:8877"])
    assert not host_allowed("http://[::ffff:127.0.0.1]:8878/", ["[::ffff:7f00:1]:8877"])


def test_public_host_children_and_plain_normalized_host_are_unchanged():
    assert normalized_host("http://localhost:8877/") == "localhost"
    assert normalized_host("https://WWW.Example.org/") == "example.org"
    assert host_allowed("https://us-west-1.console.aws.amazon.com/", ["console.aws.amazon.com"])
    assert not host_allowed("https://evilconsole.aws.amazon.com/", ["console.aws.amazon.com"])
