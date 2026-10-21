"""Фоновый обработчик регламентных задач.

В полной версии эти задачи выполняет Celery beat. Для MVP достаточно одного
процесса, который раз в интервал запускает идемпотентные функции:
повторный запуск ничего не дублирует, поэтому пропуск или двойной запуск безопасны.
"""
import logging
import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections
from django.utils import timezone

from finance.services import accrue_penalties, generate_invoices
from leasing.services import complete_expired_contracts, expire_bookings

log = logging.getLogger("scheduler")


def run_once():
    now = timezone.now()
    today = timezone.localdate(now)
    expired = expire_bookings(now)
    completed = complete_expired_contracts(today)
    invoices = generate_invoices(today)
    penalties = accrue_penalties(today)
    log.info(
        "Сняты брони: %s · завершены договоры: %s · выставлены счета: %s · пени пересчитаны: %s",
        expired, completed, len(invoices), penalties,
    )
    return expired, completed, len(invoices), penalties


class Command(BaseCommand):
    help = "Регламентные задачи: снятие просроченных броней, завершение договоров, счета, пени."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Выполнить один проход и выйти")
        parser.add_argument("--interval", type=int, default=60, help="Пауза между проходами, секунд")

    def handle(self, *args, once=False, interval=60, **options):
        if once:
            # Разовый запуск (cron, CI): ошибка должна быть видна и вернуть ненулевой код.
            expired, completed, invoices, penalties = run_once()
            self.stdout.write(
                f"Сняты брони: {expired}; завершены договоры: {completed}; "
                f"выставлены счета: {invoices}; пени пересчитаны: {penalties}"
            )
            return
        while True:
            close_old_connections()
            try:
                run_once()
            except Exception:  # долгоживущий процесс не должен падать из-за одной ошибки
                log.exception("Ошибка регламентной задачи")
            time.sleep(interval)
