import pytest
from olist_agent.agent.model import model_error_message


@pytest.mark.parametrize('code, hint', [(503, 'temporarily busy'), (429, 'quota or rate limit'), (403, 'authentication or access'), (404, 'unavailable'), (504, 'time limit')])
def test_provider_failure_hints_do_not_reflect_secret_payloads(code, hint):
    secret = 'private-api-key-value'
    outer = RuntimeError('provider request failed')
    outer.__cause__ = RuntimeError(f'{code} provider error, API key={secret}, private question payload')
    message = model_error_message(outer)
    assert hint in message
    assert secret not in message and 'private question payload' not in message
