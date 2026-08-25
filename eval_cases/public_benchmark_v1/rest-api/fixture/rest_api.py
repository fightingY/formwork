"""In-memory REST-like user API."""


class UserStore:
    def __init__(self):
        self._users = {}
        self._next_id = 1

    def create(self, name: str) -> dict:
        return {}

    def get(self, user_id: int) -> dict | None:
        return None

    def update(self, user_id: int, name: str) -> dict | None:
        return None

    def delete(self, user_id: int) -> bool:
        return False
