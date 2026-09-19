import pytest

from ctrl.credentials import CredentialError, extract_key


def test_plain_and_json_openrouter_credentials():
    assert extract_key(' sk-or-test ') == 'sk-or-test'
    assert extract_key('{"OPENROUTER_API_KEY":"sk-or-test"}') == 'sk-or-test'
    assert extract_key('{"note":"do not use", "other":"sk-or-test"}') == 'sk-or-test'


@pytest.mark.parametrize('value', [None, '', 'sk-other-provider', '{bad', '{"key":"wrong"}',
                                  '{"a":"sk-or-one","b":"sk-or-two"}'])
def test_invalid_secret_is_not_echoed(value):
    with pytest.raises(CredentialError) as error:
        extract_key(value)
    assert 'sk-or-one' not in str(error.value)
    assert 'sk-or-two' not in str(error.value)
    assert 'sk-other-provider' not in str(error.value)
