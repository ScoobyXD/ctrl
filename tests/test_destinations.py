"""Browser destinations come from user addresses or explicit site names."""

import pytest

from jev_ultrafast.navigation import navigation_urls


def test_named_sites_produce_addresses_only_in_goal_order():
    assert navigation_urls("open up my gmail, open x then scroll around on reddit") == [
        "https://mail.google.com/", "https://x.com/", "https://www.reddit.com/",
    ]


def test_explicit_urls_and_domains_work_without_a_site_registry():
    assert navigation_urls('Open https://docs.python.org/3/ then example.org/help.') == [
        "https://docs.python.org/3/", "https://example.org/help",
    ]


def test_supplied_address_resume_dedupes_exact_url():
    assert navigation_urls('Open "https://example.org/"', ["https://example.org/"]) == ["https://example.org/"]


def test_single_letter_and_email_are_not_navigation_destinations():
    assert navigation_urls("Set x to 3; email foo@example.com") == []


def test_no_generated_destination_for_an_unknown_site_name():
    assert navigation_urls("Open the Acme dashboard") == []


def test_credentials_in_url_are_not_offered():
    assert navigation_urls("Open https://user:password@example.com/") == []


def test_destination_limit_is_explicit():
    with pytest.raises(ValueError, match="16 web addresses"):
        navigation_urls("Open these", [f"https://site{i}.example/" for i in range(17)])
