"""
Tests for tools/inspect_links.py — the markdown link inspector that runs
in CI on every PR.

These tests pin the existing behavior so we can extend the inspector
without regressing.
"""

import os
import sys
import textwrap

import bs4
import pytest

# Allow `import inspect_links` regardless of where pytest is invoked from.
sys.path.insert(0, os.path.dirname(__file__))
import inspect_links


@pytest.fixture(autouse=True)
def reset_warnings():
    """Clear the module-level warnings singleton between tests."""
    inspect_links.warnings.get_and_clear()
    yield
    inspect_links.warnings.get_and_clear()


def _warns():
    return inspect_links.warnings.get()


# ---------------------------------------------------------------------------
# is_strict_title
# ---------------------------------------------------------------------------


class TestIsStrictTitle:
    def test_exact_match_is_strict(self):
        assert inspect_links.is_strict_title("updates from rust community")

    def test_case_insensitive(self):
        assert inspect_links.is_strict_title("Updates From Rust Community")
        assert inspect_links.is_strict_title("UPDATES FROM RUST COMMUNITY")

    def test_unrelated_title_is_not_strict(self):
        assert not inspect_links.is_strict_title("Crate of the Week")
        assert not inspect_links.is_strict_title("Quote of the Week")
        assert not inspect_links.is_strict_title("Jobs")

    def test_handles_non_string_input(self):
        # bs4 tag.string is sometimes None; the function coerces with str()
        # which means None becomes "None" — not strict, no crash.
        assert not inspect_links.is_strict_title(None)


# ---------------------------------------------------------------------------
# check_truncated_title
# ---------------------------------------------------------------------------


class TestCheckTruncatedTitle:
    def _link_tag(self, title):
        html = f'<a href="https://example.com">{title}</a>'
        return bs4.BeautifulSoup(html, "html.parser").a

    def test_warns_when_title_ends_with_ellipsis_at_exactly_70_chars(self):
        # 70 char string ending in "..."
        title = "a" * 67 + "..."
        assert len(title) == 70
        inspect_links.check_truncated_title(self._link_tag(title))
        assert any("truncated link title" in w for w in _warns())

    def test_no_warning_when_title_is_69_chars(self):
        title = "a" * 66 + "..."
        assert len(title) == 69
        inspect_links.check_truncated_title(self._link_tag(title))
        assert _warns() == []

    def test_no_warning_when_title_ends_without_ellipsis(self):
        title = "a" * 70
        inspect_links.check_truncated_title(self._link_tag(title))
        assert _warns() == []

    def test_no_warning_when_unicode_ellipsis_is_used(self):
        # Documented workaround: replace "..." with "…"
        title = "a" * 69 + "…"
        assert len(title) == 70
        inspect_links.check_truncated_title(self._link_tag(title))
        assert _warns() == []

    def test_handles_empty_link_title(self):
        # <a href="..."></a> has tag.string == None — should not crash.
        tag = bs4.BeautifulSoup(
            '<a href="https://example.com"></a>', "html.parser"
        ).a
        inspect_links.check_truncated_title(tag)
        assert _warns() == []


# ---------------------------------------------------------------------------
# scrub_parameters
# ---------------------------------------------------------------------------


class TestScrubParameters:
    def test_strips_utm_source_and_warns(self):
        result = inspect_links.scrub_parameters(
            "https://example.com/?utm_source=twitter", "utm_source=twitter"
        )
        assert result == ""
        assert any("tracking parameters" in w for w in _warns())

    def test_strips_all_known_utm_variants(self):
        query = "utm_source=a&utm_campaign=b&utm_medium=c&utm_content=d"
        result = inspect_links.scrub_parameters("https://example.com/?" + query, query)
        assert result == ""
        # One warning that lists all four
        assert len(_warns()) == 1
        msg = _warns()[0]
        for k in ("utm_source", "utm_campaign", "utm_medium", "utm_content"):
            assert k in msg

    def test_keeps_unknown_parameters(self):
        result = inspect_links.scrub_parameters(
            "https://example.com/?id=42", "id=42"
        )
        assert result == "id=42"
        assert _warns() == []

    def test_mixed_keeps_non_utm_and_warns_on_utm(self):
        result = inspect_links.scrub_parameters(
            "https://example.com/?id=42&utm_source=twitter",
            "id=42&utm_source=twitter",
        )
        assert result == "id=42"
        assert any("utm_source" in w for w in _warns())


# ---------------------------------------------------------------------------
# parse_url
# ---------------------------------------------------------------------------


class TestParseUrl:
    def test_https_url_passes_through_unchanged(self):
        assert (
            inspect_links.parse_url("https://example.com/path")
            == "https://example.com/path"
        )
        assert _warns() == []

    def test_http_is_normalized_to_https_without_warning(self):
        # http -> https is silent; only canonicalization that would not
        # round-trip the original gets a warning.
        assert (
            inspect_links.parse_url("http://example.com/path")
            == "https://example.com/path"
        )
        assert _warns() == []

    def test_mailto_is_accepted(self):
        # mailto links are valid; only schemes outside http/https/mailto warn.
        inspect_links.parse_url("mailto:test@example.com")
        assert not any("malformed link" in w for w in _warns())

    def test_unknown_scheme_warns(self):
        inspect_links.parse_url("ftp://example.com/file")
        assert any("possibly malformed link" in w for w in _warns())

    def test_trailing_slash_is_stripped(self):
        # Trailing slash is removed silently (no warning), because both
        # forms canonicalize to the same URL.
        assert (
            inspect_links.parse_url("https://example.com/path/")
            == "https://example.com/path"
        )

    def test_consecutive_slashes_warn_and_collapse(self):
        result = inspect_links.parse_url("https://example.com/a//b")
        assert result == "https://example.com/a/b"
        assert any("can be simplified" in w for w in _warns())

    def test_tracking_parameters_are_stripped_and_warn(self):
        result = inspect_links.parse_url(
            "https://example.com/?utm_source=twitter"
        )
        assert result == "https://example.com"
        assert any("tracking parameters" in w for w in _warns())

    def test_non_tracking_query_is_preserved(self):
        result = inspect_links.parse_url("https://example.com/?id=42")
        assert result == "https://example.com?id=42"
        assert _warns() == []


# ---------------------------------------------------------------------------
# extract_links (strict-mode header tracking)
# ---------------------------------------------------------------------------


def _md(text):
    """Render markdown to HTML the same way the inspector does."""
    import markdown

    return markdown.markdown(textwrap.dedent(text))


class TestExtractLinks:
    def test_link_outside_strict_section_is_ignored(self):
        html = _md(
            """
            ## Crate of the Week
            * [some link](https://example.com)
            """
        )
        assert inspect_links.extract_links(html) == []

    def test_link_inside_strict_section_is_collected(self):
        html = _md(
            """
            ## Updates from Rust Community
            * [some link](https://example.com)
            """
        )
        assert inspect_links.extract_links(html) == ["https://example.com"]

    def test_strict_section_ends_at_next_same_level_header(self):
        html = _md(
            """
            ## Updates from Rust Community
            * [inside](https://inside.example.com)

            ## Crate of the Week
            * [outside](https://outside.example.com)
            """
        )
        urls = inspect_links.extract_links(html)
        assert "https://inside.example.com" in urls
        assert "https://outside.example.com" not in urls

    def test_strict_section_continues_through_deeper_headers(self):
        # Sub-sections (h3, h4) inside the strict section are still strict.
        html = _md(
            """
            ## Updates from Rust Community
            ### Project/Tooling Updates
            * [proj link](https://proj.example.com)
            ### Observations
            * [obs link](https://obs.example.com)
            """
        )
        urls = inspect_links.extract_links(html)
        assert "https://proj.example.com" in urls
        assert "https://obs.example.com" in urls

    def test_strict_section_title_is_case_insensitive(self):
        html = _md(
            """
            ## UPDATES FROM RUST COMMUNITY
            * [link](https://example.com)
            """
        )
        assert inspect_links.extract_links(html) == ["https://example.com"]


# ---------------------------------------------------------------------------
# Dangling description after the link (issue #8155)
#
# TWIR convention is that the bullet should put the whole
# "title: description" inside the link text, not split across the link
# boundary. These tests document the desired warning behavior — see PR
# #8111 for real-world examples of the cleanup these will catch.
#
#   Bad : * [project_name](https://...): description
#   Good: * [project_name: description](https://...)
# ---------------------------------------------------------------------------


class TestDanglingDescription:
    def test_well_formed_bullet_does_not_warn(self):
        html = _md(
            """
            ## Updates from Rust Community
            * [project_name: Description of the project](https://example.com/x)
            """
        )
        inspect_links.extract_links(html)
        assert not any("dangling" in w.lower() for w in _warns())

    def test_bullet_with_trailing_colon_description_warns(self):
        html = _md(
            """
            ## Updates from Rust Community
            * [project_name](https://example.com/x): Description of the project
            """
        )
        inspect_links.extract_links(html)
        warns = _warns()
        assert any(
            "dangling" in w.lower() or "trailing" in w.lower() for w in warns
        ), f"expected a warning about trailing description, got: {warns}"

    def test_bullet_with_trailing_description_no_colon_warns(self):
        # OmniScope in PR #8111 had `* [name](url): Description...` but
        # also a variant where the trailing text didn't lead with a colon.
        # Anything after the closing paren beyond whitespace is bad.
        html = _md(
            """
            ## Updates from Rust Community
            * [name](https://example.com/x) trailing description text
            """
        )
        inspect_links.extract_links(html)
        warns = _warns()
        assert any(
            "dangling" in w.lower() or "trailing" in w.lower() for w in warns
        ), f"expected a warning about trailing text, got: {warns}"

    @pytest.mark.parametrize(
        "prefix",
        [
            # Most common, documented in template comments.
            "[video]",
            "[audio]",
            # Also common and well-established in the corpus.
            "[series]",
            # Language codes used for non-English content.
            "[DE]",
            "[ZH]",
            "[FR]",
            "[ES]",
            "[PT]",
            # Combinations of tags.
            "[series] [video]",
            "[ZH] [series]",
            # Case variants seen in the wild.
            "[Video]",
            "[Audio]",
        ],
    )
    def test_pre_anchor_prefix_tags_are_allowed(self, prefix):
        # The dangling-description check looks at content AFTER the link,
        # so any pre-anchor tag prefix should pass through cleanly. The
        # corpus uses a wide variety of these (video/audio/series/language
        # codes/combinations) — we don't enforce a fixed allow-list.
        html = _md(
            f"""
            ## Updates from Rust Community
            * {prefix} [Some Title](https://example.com/x)
            """
        )
        inspect_links.extract_links(html)
        assert not any(
            "dangling" in w.lower() or "trailing" in w.lower() for w in _warns()
        ), f"prefix {prefix!r} was incorrectly flagged"

    def test_multi_link_bullet_is_allowed(self):
        # Bullets connecting two related links — e.g. "Part 1 | Part 2",
        # "[A] and [B]", "[announcement] [migration guide]" — appear in
        # the corpus. The check should only flag prose AFTER the LAST
        # link in the bullet, not prose BETWEEN links.
        html = _md(
            """
            ## Updates from Rust Community
            * [Part 1](https://example.com/one) | [Part 2](https://example.com/two)
            """
        )
        inspect_links.extract_links(html)
        assert not any(
            "dangling" in w.lower() or "trailing" in w.lower() for w in _warns()
        )

    def test_dangling_description_outside_strict_section_is_ignored(self):
        # The check is scoped to strict sections, matching existing behavior
        # of the inspector. Other sections may have prose-like formatting.
        html = _md(
            """
            ## Crate of the Week
            * [name](https://example.com/x): some description that would be
              flagged in a strict section
            """
        )
        inspect_links.extract_links(html)
        assert not any(
            "dangling" in w.lower() or "trailing" in w.lower() for w in _warns()
        )

    def test_warning_identifies_the_offending_link(self):
        # The warning should include enough info to find the bullet —
        # at minimum the URL or the link title.
        html = _md(
            """
            ## Updates from Rust Community
            * [ex_ratatui](https://hexdocs.pm/ex_ratatui): Elixir bindings for ratatui
            """
        )
        inspect_links.extract_links(html)
        bad_warns = [
            w for w in _warns()
            if "dangling" in w.lower() or "trailing" in w.lower()
        ]
        assert bad_warns, "expected at least one dangling-description warning"
        joined = " ".join(bad_warns)
        assert (
            "ex_ratatui" in joined or "hexdocs.pm/ex_ratatui" in joined
        ), f"warning should identify the bullet, got: {bad_warns}"

    def test_multiple_bad_bullets_each_warn(self):
        # PR #8111 had several bad bullets in one section — each should be
        # flagged individually, not collapsed into one warning.
        html = _md(
            """
            ## Updates from Rust Community
            * [name_one](https://example.com/one): first description
            * [name_two](https://example.com/two): second description
            * [name_three](https://example.com/three): third description
            """
        )
        inspect_links.extract_links(html)
        bad_warns = [
            w for w in _warns()
            if "dangling" in w.lower() or "trailing" in w.lower()
        ]
        assert len(bad_warns) == 3, (
            f"expected 3 dangling-description warnings, got {len(bad_warns)}: "
            f"{bad_warns}"
        )

    def test_short_trailing_punctuation_is_allowed(self):
        # A stray period or comma right after the closing paren is harmless
        # punctuation, not a description. Don't false-positive on it.
        html = _md(
            """
            ## Updates from Rust Community
            * [some link](https://example.com/x).
            """
        )
        inspect_links.extract_links(html)
        assert not any(
            "dangling" in w.lower() or "trailing" in w.lower() for w in _warns()
        )


# ---------------------------------------------------------------------------
# inspect_file / inspect_files (duplicate detection)
# ---------------------------------------------------------------------------


class TestInspectFiles:
    def _write(self, path, body):
        path.write_text(textwrap.dedent(body))
        return str(path)

    def test_no_warnings_for_unique_links(self, tmp_path):
        f1 = self._write(
            tmp_path / "2026-01-01-this-week-in-rust.md",
            """
            ## Updates from Rust Community
            * [a](https://example.com/a)
            * [b](https://example.com/b)
            """,
        )
        inspect_links.inspect_files([f1])
        assert _warns() == []

    def test_duplicate_within_same_file_is_flagged(self, tmp_path):
        f1 = self._write(
            tmp_path / "2026-01-01-this-week-in-rust.md",
            """
            ## Updates from Rust Community
            * [same](https://example.com)
            * [same again](https://example.com)
            """,
        )
        inspect_links.inspect_files([f1])
        assert any("possible duplicate link" in w for w in _warns())

    def test_duplicate_across_files_is_flagged(self, tmp_path):
        f1 = self._write(
            tmp_path / "2026-01-01-this-week-in-rust.md",
            """
            ## Updates from Rust Community
            * [a](https://example.com)
            """,
        )
        f2 = self._write(
            tmp_path / "2026-01-08-this-week-in-rust.md",
            """
            ## Updates from Rust Community
            * [a again](https://example.com)
            """,
        )
        inspect_links.inspect_files([f1, f2])
        warns = _warns()
        assert any(
            "possible duplicate link" in w and "example.com" in w for w in warns
        )

    def test_duplicate_outside_strict_section_is_ignored(self, tmp_path):
        # Links in non-strict sections (e.g. the masthead, "Jobs", etc.) may
        # repeat across issues without warning.
        f1 = self._write(
            tmp_path / "2026-01-01-this-week-in-rust.md",
            """
            ## Jobs
            * [shared](https://repeat.example.com)
            """,
        )
        f2 = self._write(
            tmp_path / "2026-01-08-this-week-in-rust.md",
            """
            ## Jobs
            * [shared](https://repeat.example.com)
            """,
        )
        inspect_links.inspect_files([f1, f2])
        assert _warns() == []


# ---------------------------------------------------------------------------
# get_recent_files
# ---------------------------------------------------------------------------


class TestGetRecentFiles:
    def _touch(self, dir, names):
        for name in names:
            (dir / name).write_text("")

    def test_returns_most_recent_n_by_filename_sort(self, tmp_path):
        self._touch(
            tmp_path,
            [
                "2026-01-01-this-week-in-rust.md",
                "2026-02-01-this-week-in-rust.md",
                "2026-03-01-this-week-in-rust.md",
                "README.md",  # should be filtered out
            ],
        )
        files = inspect_links.get_recent_files(str(tmp_path), 2)
        assert [os.path.basename(f) for f in files] == [
            "2026-02-01-this-week-in-rust.md",
            "2026-03-01-this-week-in-rust.md",
        ]

    def test_filters_filenames_that_dont_match_format(self, tmp_path):
        self._touch(
            tmp_path,
            [
                "2026-01-01-this-week-in-rust.md",
                "notes.md",
                "some-other-file.md",
            ],
        )
        files = inspect_links.get_recent_files(str(tmp_path), 10)
        assert [os.path.basename(f) for f in files] == [
            "2026-01-01-this-week-in-rust.md"
        ]

    def test_multiple_paths_are_combined_and_sorted(self, tmp_path):
        content = tmp_path / "content"
        draft = tmp_path / "draft"
        content.mkdir()
        draft.mkdir()
        self._touch(content, ["2026-01-01-this-week-in-rust.md"])
        self._touch(draft, ["2026-02-01-this-week-in-rust.md"])
        files = inspect_links.get_recent_files(
            f"{content}:{draft}", 10
        )
        names = [os.path.basename(f) for f in files]
        assert names == [
            "2026-01-01-this-week-in-rust.md",
            "2026-02-01-this-week-in-rust.md",
        ]

    def test_raises_when_directory_has_no_matching_files(self, tmp_path):
        self._touch(tmp_path, ["README.md"])
        with pytest.raises(Exception, match="No matching files"):
            inspect_links.get_recent_files(str(tmp_path), 5)

    def test_raises_when_directory_is_empty(self, tmp_path):
        with pytest.raises(Exception, match="No files"):
            inspect_links.get_recent_files(str(tmp_path), 5)
