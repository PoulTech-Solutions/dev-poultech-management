from enum import StrEnum


class RoleName(StrEnum):
    SUPER_ADMIN = 'super_admin'
    ADMIN = 'admin'
    STAFF = 'staff'
    FARM_OWNER = 'farm_owner'
    FARM_MEMBER = 'farm_member'
    USER = 'user'


DEFAULT_ROLE = RoleName.USER
