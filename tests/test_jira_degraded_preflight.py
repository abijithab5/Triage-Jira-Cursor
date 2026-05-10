"""Tests for Jira REST preflight when GET /myself is forbidden but issue reads work."""

from __future__ import annotations

from jira_triage.jira_client import degraded_preflight_myself_allows_issue_fetch


def test_degraded_path_when_seraph_ok_on_403():
    assert degraded_preflight_myself_allows_issue_fetch(
        {"status_code": 403, "x_seraph_loginreason": "OK", "has_x_ausername": False}
    )


def test_degraded_path_when_username_header_on_403():
    assert degraded_preflight_myself_allows_issue_fetch(
        {"status_code": 403, "x_seraph_loginreason": "", "has_x_ausername": True}
    )


def test_no_degraded_on_401():
    assert not degraded_preflight_myself_allows_issue_fetch(
        {"status_code": 401, "x_seraph_loginreason": "OK"}
    )


def test_no_degraded_on_403_without_auth_signals():
    assert not degraded_preflight_myself_allows_issue_fetch(
        {"status_code": 403, "x_seraph_loginreason": "", "has_x_ausername": False}
    )


def test_no_degraded_on_200():
    assert not degraded_preflight_myself_allows_issue_fetch({"status_code": 200, "ok": True})
