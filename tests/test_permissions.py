
import pytest

from workbench.permissions import codex_permissions, claude_needs_human


def test_auto_uses_native_reviewer_without_full_access():
    assert codex_permissions("auto") == {
        "approvalPolicy": "on-request",
        "sandbox": "workspace-write",
        "approvalsReviewer": "auto_review",
    }
    assert codex_permissions("ask")["approvalsReviewer"] == "user"
    assert codex_permissions("auto", False) == {
        "approvalPolicy": "on-request",
        "sandbox": "workspace-write",
    }
    with pytest.raises(ValueError):
        codex_permissions("bypassPermissions")


def test_claude_auto_defers_project_tools_to_native_classifier(tmp_path):
    assert not claude_needs_human(
        "auto", "Write", {"file_path": str(tmp_path / "report.md")}, str(tmp_path)
    )
    assert not claude_needs_human(
        "auto", "Bash", {"command": "npm run build"}, str(tmp_path)
    )
    assert claude_needs_human(
        "ask", "Write", {"file_path": str(tmp_path / "report.md")}, str(tmp_path)
    )
    for value in (
        "../secret",
        ".native/auth.json",
        ".env",
        str(tmp_path.parent / "outside.txt"),
    ):
        assert claude_needs_human("auto", "Read", {"file_path": value}, str(tmp_path))
    assert claude_needs_human(
        "auto", "mcp__custom__send", {"message": "hello"}, str(tmp_path)
    )


def test_claude_symlink_does_not_inherit_native_auto_permission(tmp_path):
    destination = tmp_path / "linked"
    try:
        destination.symlink_to(tmp_path.parent, target_is_directory=True)
    except OSError:
        pytest.skip("OS does not grant symlink creation")
    assert claude_needs_human(
        "auto", "Write", {"file_path": str(destination / "outside.txt")}, str(tmp_path)
    )
