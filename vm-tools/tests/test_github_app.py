"""Unit tests for the permission-intersection fix (no network needed)."""

from vm_tools.github_app import DEFAULT_PERMISSIONS, intersect_permissions


def test_drops_ungranted_permission():
    # The real install grants only contents + metadata -> PR permission dropped.
    granted = {"contents": "write", "metadata": "read"}
    assert intersect_permissions(DEFAULT_PERMISSIONS, granted) == {
        "contents": "write",
        "metadata": "read",
    }


def test_keeps_granted_pr_permission():
    granted = {"contents": "write", "metadata": "read", "pull_requests": "write"}
    assert intersect_permissions(DEFAULT_PERMISSIONS, granted) == DEFAULT_PERMISSIONS


def test_downgrades_level():
    assert intersect_permissions({"contents": "write"}, {"contents": "read"}) == {
        "contents": "read"
    }


def test_no_grants_returns_empty():
    assert intersect_permissions(DEFAULT_PERMISSIONS, {}) == {}
