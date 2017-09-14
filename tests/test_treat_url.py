# pylint: disable=blacklisted-name

import logging

import pytest
from mock import MagicMock

import urlnorm

import libci
from libci.utils import treat_url


@pytest.mark.parametrize('url, expected', [
    # Add more patterns bellow when necessary
    ('HTTP://FoO.bAr.coM././foo/././../foo/index.html', 'http://foo.bar.com/foo/index.html'),
    # urlnorm cannot handle localhost but treat_url should handle such situation
    ('http://localhost/index.html', 'http://localhost/index.html')
])
def test_sanity(url, expected):
    assert treat_url(url, shorten=False) == expected


def test_urlnorm_error(monkeypatch):
    monkeypatch.setattr(urlnorm, 'norm', MagicMock(side_effect=urlnorm.InvalidUrl))

    with pytest.raises(urlnorm.InvalidUrl):
        treat_url('dummy url')

    # pylint: disable=no-member
    urlnorm.norm.assert_called_once_with('dummy url')


def test_shortening(logger, monkeypatch):
    mock_return = (None, 'dummy shortened URL from http://foo.bar.com/')
    monkeypatch.setattr(libci.utils, 'fetch_url', MagicMock(return_value=mock_return))

    assert treat_url('http://foo.bar.com/', shorten=True) == 'dummy shortened URL from http://foo.bar.com/'
    # pylint: disable=no-member
    libci.utils.fetch_url.assert_called_once_with('https://url.corp.redhat.com/new?http://foo.bar.com/', logger=logger)


def test_shortening_errors(log, logger, monkeypatch):
    def throw(*args, **kwargs):
        # pylint: disable=unused-argument

        raise libci.CIError('simply bad request')

    monkeypatch.setattr(libci.utils, 'fetch_url', MagicMock(side_effect=throw))

    assert treat_url('http://foo.bar.com/', shorten=True, logger=logger) == 'http://foo.bar.com/'

    assert len(log.records) == 2
    assert log.records[0].message == "treating a URL 'http://foo.bar.com/'"
    assert log.records[1].levelno == logging.WARN
    assert log.records[1].message == 'Unable to shorten URL (see log for more details): simply bad request'


def test_strip(monkeypatch):
    monkeypatch.setattr(libci.utils, 'fetch_url', MagicMock(return_value=(None, '   so much whitespace   ')))

    assert treat_url('http://foo.bar.com/', shorten=True) == 'so much whitespace'
