from organic_mvp.evidence import DuckDuckGoProvider


class _FakeHTTP:
    def get(self, url, headers=None):
        del url, headers
        return (
            b'<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fpython.org%2Fdownloads%2F">Python Downloads</a>',
            {},
        )


def test_keyless_search_unwraps_duckduckgo_target_url():
    results = DuckDuckGoProvider(_FakeHTTP(), logger=None).search("latest Python", limit=3)

    assert len(results) == 1
    assert results[0].url == "https://python.org/downloads/"
    assert results[0].provider == "duckduckgo"
