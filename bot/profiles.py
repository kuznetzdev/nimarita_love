from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

from config import USER_PROFILES_PATH

logger = logging.getLogger(__name__)

ProfileRole = Literal["boyfriend", "girlfriend", "self", "unknown"]
ProfileGender = Literal["male", "female", "unknown"]

PROFILES_PATH = USER_PROFILES_PATH
ROLE_VALUES: tuple[ProfileRole, ...] = ("boyfriend", "girlfriend", "self", "unknown")
GENDER_VALUES: tuple[ProfileGender, ...] = ("male", "female", "unknown")
ROLE_LOOKUP: dict[str, ProfileRole] = {role: role for role in ROLE_VALUES}
GENDER_LOOKUP: dict[str, ProfileGender] = {gender: gender for gender in GENDER_VALUES}


@dataclass(slots=True, frozen=True)
class UserProfile:
    id: int
    label: str
    role: ProfileRole
    gender: ProfileGender

    def to_public_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "label": self.label,
            "role": self.role,
            "gender": self.gender,
        }


class UserProfileRegistry:
    def __init__(self, profiles: list[UserProfile]) -> None:
        self._profiles_by_id = {profile.id: profile for profile in profiles}
        self._profiles_by_label = {
            profile.label.casefold(): profile
            for profile in profiles
            if profile.label.strip()
        }
        self._profiles_by_role: dict[ProfileRole, list[UserProfile]] = {
            role: [] for role in ROLE_VALUES
        }
        for profile in profiles:
            self._profiles_by_role[profile.role].append(profile)

    @classmethod
    def from_file(cls, path: Path) -> "UserProfileRegistry":
        if not path.exists():
            logger.info("Profiles file '%s' not found. Registry is empty.", path)
            return cls([])

        try:
            raw_payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            logger.exception("Profiles file '%s' is not valid JSON. Registry is empty.", path)
            return cls([])

        if not isinstance(raw_payload, dict):
            logger.error("Profiles file '%s' must contain a JSON object.", path)
            return cls([])

        raw_profiles = raw_payload.get("profiles", [])
        if not isinstance(raw_profiles, list):
            logger.error("Profiles file '%s' must contain a 'profiles' list.", path)
            return cls([])

        profiles: list[UserProfile] = []
        for raw_profile in raw_profiles:
            if not isinstance(raw_profile, dict):
                logger.warning("Skipping malformed profile entry: %r", raw_profile)
                continue
            profile = _parse_profile(raw_profile)
            if profile is not None:
                profiles.append(profile)

        return cls(profiles)

    def is_configured(self) -> bool:
        return bool(self._profiles_by_id)

    def is_allowed_user(self, user_id: int) -> bool:
        return user_id in self._profiles_by_id

    def get(self, user_id: int) -> UserProfile | None:
        return self._profiles_by_id.get(user_id)

    def resolve(self, token: object) -> UserProfile | None:
        if isinstance(token, int):
            return self.get(token)

        if isinstance(token, str):
            stripped = token.strip()
            if not stripped:
                return None

            try:
                return self.get(int(stripped))
            except ValueError:
                pass

            by_label = self._profiles_by_label.get(stripped.casefold())
            if by_label is not None:
                return by_label

            normalized_role = _normalize_role(stripped)
            if normalized_role is not None:
                role_profiles = self._profiles_by_role[normalized_role]
                if len(role_profiles) == 1:
                    return role_profiles[0]
        return None

    def available_recipients(self) -> list[dict[str, object]]:
        profiles = list(self._profiles_by_id.values())
        profiles.sort(key=lambda item: (item.label.casefold(), item.id))
        return [profile.to_public_dict() for profile in profiles]


@lru_cache(maxsize=1)
def load_user_profiles(path: Path = PROFILES_PATH) -> UserProfileRegistry:
    return UserProfileRegistry.from_file(path)


def _parse_profile(raw_profile: dict[str, object]) -> UserProfile | None:
    profile_id = _read_int(raw_profile.get("id"))
    label = _read_text(raw_profile.get("label"))
    role = _normalize_role(_read_text(raw_profile.get("role"), default="unknown")) or "unknown"
    gender = _normalize_gender(_read_text(raw_profile.get("gender"), default="unknown")) or "unknown"

    if profile_id <= 0:
        logger.warning("Skipping profile with invalid id: %r", raw_profile)
        return None
    if not label:
        logger.warning("Skipping profile with empty label: %r", raw_profile)
        return None

    return UserProfile(
        id=profile_id,
        label=label,
        role=role,
        gender=gender,
    )


def _read_text(value: object, default: str = "") -> str:
    if isinstance(value, str):
        return value.strip()
    if value is None:
        return default
    return str(value).strip()


def _read_int(value: object) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return 0
    return 0


def _normalize_role(value: str) -> ProfileRole | None:
    return ROLE_LOOKUP.get(value.strip().casefold())


def _normalize_gender(value: str) -> ProfileGender | None:
    return GENDER_LOOKUP.get(value.strip().casefold())

