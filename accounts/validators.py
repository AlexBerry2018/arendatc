"""Проверка реквизитов российских организаций по контрольным суммам.

Реальная проверка через API ФНС в MVP заменена проверкой контрольных разрядов:
она отсекает опечатки, но не подтверждает существование организации.
"""
from django.core.exceptions import ValidationError

_INN10_WEIGHTS = (2, 4, 10, 3, 5, 9, 4, 6, 8)
_INN11_WEIGHTS = (7, 2, 4, 10, 3, 5, 9, 4, 6, 8)
_INN12_WEIGHTS = (3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8)


def _checksum(digits, weights):
    return sum(d * w for d, w in zip(digits, weights)) % 11 % 10


def is_valid_inn(value: str) -> bool:
    if not value or not value.isdigit() or len(value) not in (10, 12):
        return False
    d = [int(c) for c in value]
    if len(d) == 10:
        return _checksum(d, _INN10_WEIGHTS) == d[9]
    return _checksum(d, _INN11_WEIGHTS) == d[10] and _checksum(d, _INN12_WEIGHTS) == d[11]


def is_valid_ogrn(value: str) -> bool:
    """ОГРН (13 цифр) или ОГРНИП (15 цифр)."""
    if not value or not value.isdigit() or len(value) not in (13, 15):
        return False
    if len(value) == 13:
        return int(value[:12]) % 11 % 10 == int(value[12])
    return int(value[:14]) % 13 % 10 == int(value[14])


def complete_inn(base: str) -> str:
    """Дописывает контрольные разряды к 9 (юрлицо) или 10 (ИП) цифрам."""
    d = [int(c) for c in base]
    if len(d) == 9:
        d.append(_checksum(d, _INN10_WEIGHTS))
    elif len(d) == 10:
        d.append(_checksum(d, _INN11_WEIGHTS))
        d.append(_checksum(d, _INN12_WEIGHTS))
    else:
        raise ValueError("Нужно 9 или 10 цифр")
    return "".join(map(str, d))


def complete_ogrn(base: str) -> str:
    """Дописывает контрольный разряд к 12 (ОГРН) или 14 (ОГРНИП) цифрам."""
    if len(base) == 12:
        return base + str(int(base) % 11 % 10)
    if len(base) == 14:
        return base + str(int(base) % 13 % 10)
    raise ValueError("Нужно 12 или 14 цифр")


def validate_inn(value: str):
    if not is_valid_inn(value):
        raise ValidationError("Некорректный ИНН: нужно 10 цифр для организации или 12 для ИП, с верной контрольной суммой.")


def validate_ogrn(value: str):
    if not is_valid_ogrn(value):
        raise ValidationError("Некорректный ОГРН/ОГРНИП: нужно 13 или 15 цифр с верной контрольной суммой.")
