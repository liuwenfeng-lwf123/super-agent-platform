"""Unit tests for RBAC module."""
import json
import os
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from app.security.rbac import (
    Role,
    User,
    authenticate_token,
    check_permission,
    get_required_permission,
    reload_users,
    _load_users,
)


class TestRolePermissions(unittest.TestCase):
    def test_admin_has_all(self):
        user = User(username="admin", token="t", role=Role.ADMIN)
        self.assertTrue(check_permission(user, "read"))
        self.assertTrue(check_permission(user, "write"))
        self.assertTrue(check_permission(user, "execute"))
        self.assertTrue(check_permission(user, "admin"))

    def test_operator_no_admin(self):
        user = User(username="op", token="t", role=Role.OPERATOR)
        self.assertTrue(check_permission(user, "read"))
        self.assertTrue(check_permission(user, "write"))
        self.assertTrue(check_permission(user, "execute"))
        self.assertFalse(check_permission(user, "admin"))

    def test_viewer_read_only(self):
        user = User(username="v", token="t", role=Role.VIEWER)
        self.assertTrue(check_permission(user, "read"))
        self.assertFalse(check_permission(user, "write"))
        self.assertFalse(check_permission(user, "execute"))
        self.assertFalse(check_permission(user, "admin"))


class TestGetRequiredPermission(unittest.TestCase):
    def test_get_is_read(self):
        self.assertEqual(get_required_permission("GET", "/api/threads"), "read")

    def test_post_is_write(self):
        self.assertEqual(get_required_permission("POST", "/api/memory"), "write")

    def test_delete_is_admin(self):
        self.assertEqual(get_required_permission("DELETE", "/api/threads/123"), "admin")

    def test_chat_override(self):
        self.assertEqual(get_required_permission("POST", "/api/chat/send"), "execute")

    def test_local_send_override(self):
        self.assertEqual(get_required_permission("POST", "/api/local/send"), "execute")


class TestAuthenticateToken(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.users_file = os.path.join(self.tmp, "users.json")
        users_data = [
            {"username": "alice", "token": "alice-token", "role": "admin", "display_name": "Alice"},
            {"username": "bob", "token": "bob-token", "role": "viewer"},
        ]
        with open(self.users_file, "w") as f:
            json.dump(users_data, f)

    def test_valid_token(self):
        import app.security.rbac as rbac
        rbac._users_cache = None
        with patch.object(rbac, "_USERS_FILE", self.users_file):
            rbac._users_cache = None
            rbac._load_users()
            user = authenticate_token("alice-token")
            self.assertIsNotNone(user)
            self.assertEqual(user.username, "alice")
            self.assertEqual(user.role, Role.ADMIN)

    def test_invalid_token(self):
        import app.security.rbac as rbac
        rbac._users_cache = None
        with patch.object(rbac, "_USERS_FILE", self.users_file):
            rbac._users_cache = None
            rbac._load_users()
            user = authenticate_token("wrong-token")
            self.assertIsNone(user)

    def test_viewer_role(self):
        import app.security.rbac as rbac
        rbac._users_cache = None
        with patch.object(rbac, "_USERS_FILE", self.users_file):
            rbac._users_cache = None
            rbac._load_users()
            user = authenticate_token("bob-token")
            self.assertIsNotNone(user)
            self.assertEqual(user.role, Role.VIEWER)


class TestReloadUsers(unittest.TestCase):
    def test_reload_clears_cache(self):
        import app.security.rbac as rbac
        rbac._users_cache = [User(username="x", token="x", role=Role.VIEWER)]
        # After reload with no file, should reset
        with patch.object(rbac, "_USERS_FILE", "/nonexistent/users.json"):
            reload_users()
        self.assertEqual(rbac._users_cache, [])


if __name__ == "__main__":
    unittest.main()
