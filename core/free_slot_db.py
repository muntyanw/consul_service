from threading import Lock
from bot_io.yaml_loader import UserConfig

class FreeSlotRegistry:
    """Регистрирует найденные слоты и управляет ими."""

    def __init__(self):
        self._slots: dict[str, dict[str, dict[str, list[str]]]] = {}
        self._lock = Lock()

    def add(self, country: str, cons: str, service: str, date: str):
        with self._lock:
            self._slots.setdefault(country, {}).setdefault(cons, {}).setdefault(service, [])
            if date not in self._slots[country][cons][service]:
                self._slots[country][cons][service].append(date)

    def remove(self, country: str, cons: str, service: str, date: str):
        with self._lock:
            try:
                self._slots[country][cons][service].remove(date)
                if not self._slots[country][cons][service]:
                    del self._slots[country][cons][service]
            except (KeyError, ValueError):
                pass

    def get_all(self) -> dict:
        with self._lock:
            return dict(self._slots)

    def has_match(self, user: UserConfig) -> bool:
        for cons in user.consulates:
            for service in user.services:
                if (user.country in self._slots and
                    cons in self._slots[user.country] and
                    service in self._slots[user.country][cons]):
                    return True
        return False
