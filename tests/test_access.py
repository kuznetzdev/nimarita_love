from __future__ import annotations

from bot.access import AccessDeniedError, AccessManager
from bot.profiles import UserProfile, UserProfileRegistry


def test_profiles_registry_is_primary_allowlist() -> None:
    registry = UserProfileRegistry(
        [
            UserProfile(id=101, label="Nick", role="boyfriend", gender="male"),
        ]
    )
    access = AccessManager(
        profiles=registry,
        allowed_user_ids=frozenset(),
        allowed_chat_ids=frozenset(),
    )

    profile = access.ensure_allowed(user_id=101, chat_id=101)

    assert profile is not None
    assert profile.label == "Nick"

    try:
        access.ensure_allowed(user_id=202, chat_id=202)
    except AccessDeniedError as error:
        assert "profiles.json" in str(error)
    else:
        raise AssertionError("Expected profiles.json allowlist to reject unknown users.")


def test_env_allowlist_is_used_when_profiles_are_empty() -> None:
    access = AccessManager(
        profiles=UserProfileRegistry([]),
        allowed_user_ids=frozenset({7}),
        allowed_chat_ids=frozenset(),
    )

    assert access.ensure_allowed(user_id=7, chat_id=70) is None

    try:
        access.ensure_allowed(user_id=8, chat_id=80)
    except AccessDeniedError as error:
        assert "ALLOWED_USER_IDS" in str(error)
    else:
        raise AssertionError("Expected ALLOWED_USER_IDS to reject unknown users.")


def test_recipient_can_be_resolved_by_unique_role() -> None:
    registry = UserProfileRegistry(
        [
            UserProfile(id=11, label="Nick", role="boyfriend", gender="male"),
            UserProfile(id=12, label="Margarette", role="girlfriend", gender="female"),
        ]
    )
    access = AccessManager(
        profiles=registry,
        allowed_user_ids=frozenset(),
        allowed_chat_ids=frozenset(),
    )

    assert access.resolve_recipient("boyfriend") == 11
    assert access.resolve_recipient("Margarette") == 12
    assert access.resolve_recipient("12") == 12
