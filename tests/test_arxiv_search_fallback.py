"""Fixed-host arXiv HTML fallback: parser and failure boundaries."""
import httpx
import pytest

from nima_semantica.arxiv_search_fallback import parse_search_page, search_page


PAGE = """<html><ol>
<li class="arxiv-result"><p class="list-title"><a href="https://arxiv.org/abs/2609.12345">arXiv:2609.12345</a></p><p class="title">Pauli propagation with truncation</p><span class="abstract-full">A bounded Pauli propagation method.</span></li>
<li class="arxiv-result"><p class="list-title"><a href="https://evil.example/abs/2609.54321">arXiv:2609.54321</a></p><p class="title">Impersonation</p></li>
<li class="arxiv-result"><p class="list-title"><a href="https://arxiv.org/abs/2609.23456">arXiv:2609.23456</a></p><p class="title">Other subject</p><span class="abstract-full">General methods.</span></li>
</ol></html>"""


def test_parser_rejects_foreign_host_and_ranks_matching_result():
    rows = parse_search_page(PAGE, "Pauli propagation", max_results=2)
    assert [row["id"] for row in rows] == ["https://arxiv.org/abs/2609.12345", "https://arxiv.org/abs/2609.23456"]
    assert all(row["source"] == "arxiv_search_html_fallback" for row in rows)


def test_fixed_host_request_no_redirect_and_bounded_query():
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(200, text=PAGE, headers={"content-type": "text/html"})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        rows = search_page("Pauli propagation", client=client)
    assert rows[0]["title"] == "Pauli propagation with truncation"
    assert len(seen) == 1 and seen[0].url.host == "arxiv.org"
    assert seen[0].url.path == "/search/" and seen[0].url.params["query"] == "Pauli propagation"
    with pytest.raises(ValueError, match="short printable"):
        search_page("x" * 161, client=client)


@pytest.mark.parametrize("status,content_type", [(406, "text/html"), (302, "text/html"), (200, "application/json")])
def test_unavailable_or_wrong_media_type_fails_closed(status, content_type):
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(status, text=PAGE,
        headers={"content-type": content_type}))) as client:
        with pytest.raises(RuntimeError):
            search_page("Pauli propagation", client=client)
