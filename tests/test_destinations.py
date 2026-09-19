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


def test_youtube_and_aws_are_addresses_not_action_workflows():
    assert navigation_urls("Open YouTube then go in AWS") == [
        "https://www.youtube.com/", "https://console.aws.amazon.com/",
    ]


@pytest.mark.parametrize("goal,values", [
    ("Open YouTube and search for quiet jazz then open the first video", ["quiet jazz"]),
    ("Search Wikipedia for Ada Lovelace and open the article.", ["Ada Lovelace"]),
    ("Search for Python 3.12 tutorials.", ["Python 3.12 tutorials"]),
    ("Type Ada Lovelace into the name field", ["Ada Lovelace"]),
    ("Enter jon@example.org in the email box", ["jon@example.org"]),
    ('Search for "exact phrase"', []),
    ("Open Gmail and read 100 emails", []),
])
def test_natural_instruction_values_are_exact_user_spans(goal, values):
    from jev_ultrafast.navigation import task_text_values

    result = task_text_values(goal)
    assert result == values
    assert all(value in goal for value in result)


@pytest.mark.parametrize("prefix", ["do not", "don't", "never", "avoid", "not"])
def test_negated_text_commands_do_not_create_supplied_values(prefix):
    from jev_ultrafast.navigation import task_text_values

    assert task_text_values(f"Find cats; {prefix} type quotes") == []


def test_query_with_apostrophe_is_preserved_and_first_for_sets_span():
    from jev_ultrafast.navigation import task_text_values

    assert task_text_values("Search for O'Reilly books for beginners") == ["O'Reilly books for beginners"]
