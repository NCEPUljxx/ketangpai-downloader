from ktp_core.url_resolve import resolve_direct_url, safe_filename


def test_resolve_document_proxy_furl():
    raw = (
        "https://document.ketangpai.com/?furl="
        + "http%3A//ketangpai-test.oss-cn-hangzhou-internal.aliyuncs.com/foo/bar.pptx%3Fa%3D1"
    )
    out = resolve_direct_url(raw)
    assert out is not None
    assert "-cn-hangzhou-internal" not in out
    assert "document.ketangpai.com" not in out
    assert "bar.pptx" in out


def test_resolve_plain_passthrough():
    u = "http://example.com/file.pdf"
    assert resolve_direct_url(u) == u


def test_safe_filename_strip_invalid():
    assert "?" not in safe_filename('aa?.pptx')
    assert safe_filename("") == "download"
