# pylint: disable=blacklisted-name

import logging

import pytest
import urlnorm

import libci
from libci.utils import treat_url


def test_sanity():
    # Add more patterns bellow when necessary
    urls = {
        'HTTP://FoO.bAr.coM././foo/././../foo/index.html': 'http://foo.bar.com/foo/index.html',
        # urlnorm cannot handle localhost but treat_url should handle such situation
        'http://localhost/index.html': 'http://localhost/index.html'
    }

    for original, expected in urls.iteritems():
        assert treat_url(original, shorten=False) == expected


def test_urlnorm_errors(monkeypatch):
    def norm(url):
        # pylint: disable=unused-argument
        assert url == 'dummy url'

        raise urlnorm.InvalidUrl('dummy exception')

    monkeypatch.setattr(urlnorm, 'norm', norm)

    with pytest.raises(urlnorm.InvalidUrl, match=r'dummy exception'):
        treat_url('dummy url')


def test_shortening(monkeypatch):
    def fetch_shorten(url, **kwargs):
        # pylint: disable=unused-argument
        assert url.endswith('http://foo.bar.com/')

        return None, 'dummy shortened URL from {}'.format(url.split('?')[-1])

    monkeypatch.setattr(libci.utils, 'fetch_url', fetch_shorten)

    assert treat_url('http://foo.bar.com/', shorten=True) == 'dummy shortened URL from http://foo.bar.com/'


def test_shortening_errors(log, logger, monkeypatch):
    def fetch_raise(url, **kwargs):
        # pylint: disable=unused-argument
        raise libci.CIError('simply bad request')

    monkeypatch.setattr(libci.utils, 'fetch_url', fetch_raise)
    assert treat_url('http://foo.bar.com/', shorten=True, logger=logger) == 'http://foo.bar.com/'

    assert len(log.records) == 1
    assert log.records[0].levelno == logging.WARN
    assert log.records[0].message == 'Unable to shorten URL (see log for more details): simply bad request'


def test_strip(monkeypatch):
    # check the final strip() call
    def fetch_whitespace(url, **kwargs):
        # pylint: disable=unused-argument
        return None, '   so much whitespace   '

    monkeypatch.setattr(libci.utils, 'fetch_url', fetch_whitespace)

    assert treat_url('http://foo.bar.com/', shorten=True) == 'so much whitespace'
