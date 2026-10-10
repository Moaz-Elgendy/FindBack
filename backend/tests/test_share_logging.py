import logging
from app.log_filters import install_log_filters


def test_share_tokens_are_masked_without_breaking_uvicorn_access_arguments():
    install_log_filters()
    record = logging.LogRecord('uvicorn.access', logging.INFO, '', 0,
        '%s - "%s %s HTTP/%s" %s',
        ('phone', 'POST', '/api/v1/shares/' + 'a' * 43 + '/redeem', '1.1', 200), None)
    logger = logging.getLogger('uvicorn.access')
    assert logger.filter(record)
    assert 'a' * 43 not in record.getMessage()
    assert record.args[0] == 'phone'
    assert record.args[1] == 'POST'
    assert record.args[3:] == ('1.1', 200)
