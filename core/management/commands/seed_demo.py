"""Демонстрационные данные: 3 этажа, 30 помещений, 6 ролей, договоры, счета и оплаты."""
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from accounts.models import Tenant, User
from accounts.validators import complete_inn, complete_ogrn
from catalog.models import Floor, Premises, PremisesType
from finance.models import Invoice, Payment
from finance.services import accrue_penalties, allocate_payment, create_deposit_invoice, generate_invoices, month_start
from leasing import services as leasing
from leasing.models import Application, Contract

PASSWORD = "demo12345"

# (код, тип, площадь, ставка, x, y, w, h)
FLOORS = {
    1: ("1 этаж — fashion и сервис", [
        ("1-01", "Торговое", 118.4, 3600, 20, 20, 190, 170),
        ("1-02", "Торговое", 86.2, 3800, 220, 20, 150, 170),
        ("1-03", "Торговое", 142.0, 3400, 380, 20, 200, 170),
        ("1-04", "Торговое", 64.5, 4100, 590, 20, 140, 170),
        ("1-05", "Торговое", 205.7, 3100, 740, 20, 240, 170),
        ("1-06", "Торговое", 160.3, 3300, 20, 330, 240, 160),
        ("1-07", "Торговое", 92.0, 3700, 270, 330, 170, 160),
        ("1-08", "Торговое", 95.6, 3700, 450, 330, 170, 160),
        ("1-09", "Торговое", 248.0, 2900, 630, 330, 350, 160),
        ("1-О1", "Островное", 9.0, 6500, 300, 222, 100, 80),
        ("1-О2", "Островное", 9.0, 6500, 600, 222, 100, 80),
    ]),
    2: ("2 этаж — электроника и дом", [
        ("2-01", "Торговое", 132.0, 2900, 20, 20, 210, 170),
        ("2-02", "Торговое", 74.8, 3200, 240, 20, 150, 170),
        ("2-03", "Торговое", 110.5, 3000, 400, 20, 190, 170),
        ("2-04", "Торговое", 180.0, 2700, 600, 20, 380, 170),
        ("2-05", "Торговое", 98.0, 3100, 20, 330, 200, 160),
        ("2-06", "Торговое", 121.3, 2950, 230, 330, 210, 160),
        ("2-07", "Торговое", 66.0, 3300, 450, 330, 140, 160),
        ("2-08", "Склад", 54.0, 900, 600, 330, 150, 160),
        ("2-09", "Торговое", 140.2, 2800, 760, 330, 220, 160),
        ("2-О1", "Островное", 8.0, 5800, 450, 222, 100, 80),
    ]),
    3: ("3 этаж — фуд-корт", [
        ("3-01", "Общепит", 72.0, 3500, 20, 20, 170, 170),
        ("3-02", "Общепит", 68.5, 3500, 200, 20, 160, 170),
        ("3-03", "Общепит", 70.0, 3500, 370, 20, 160, 170),
        ("3-04", "Общепит", 81.2, 3400, 540, 20, 180, 170),
        ("3-05", "Общепит", 120.0, 3200, 730, 20, 250, 170),
        ("3-06", "Торговое", 210.0, 2400, 20, 330, 330, 160),
        ("3-07", "Торговое", 150.0, 2500, 360, 330, 260, 160),
        ("3-08", "Общепит", 95.0, 3300, 630, 330, 350, 160),
        ("3-О1", "Островное", 6.0, 6000, 450, 222, 90, 80),
    ]),
}


def add_months(d: date, months: int) -> date:
    m = d.month - 1 + months
    return date(d.year + m // 12, m % 12 + 1, 1)


class Command(BaseCommand):
    help = "Заполняет базу демонстрационными данными."

    def add_arguments(self, parser):
        parser.add_argument("--if-empty", action="store_true", help="Ничего не делать, если данные уже есть")

    def handle(self, *args, if_empty=False, **options):
        if Floor.objects.exists():
            if if_empty:
                self.stdout.write("Данные уже есть — заполнение пропущено.")
                return
            self.stderr.write("База не пуста. Используйте пустую базу или флаг --if-empty.")
            return
        with transaction.atomic():
            self._seed()
        self.stdout.write(self.style.SUCCESS(f"Демо-данные созданы. Пароль всех пользователей: {PASSWORD}"))

    # -------------------------------------------------------------------------
    def _user(self, username, role, first="", last="", **extra):
        user = User(username=username, role=role, first_name=first, last_name=last, email=f"{username}@example.ru", **extra)
        user.set_password(PASSWORD)
        user.save()
        return user

    def _tenant(self, username, legal_name, inn_base, ogrn_base, address):
        user = self._user(username, User.Role.TENANT)
        return Tenant.objects.create(
            user=user, legal_name=legal_name, inn=complete_inn(inn_base), ogrn=complete_ogrn(ogrn_base),
            address=address, phone="+7 495 000-00-00",
        )

    def _past_contract(self, tenant, code, start, months, activity, manager, director):
        premises = Premises.objects.get(code=code)
        end = add_months(start, months) - timedelta(days=1)
        app = Application.objects.create(
            tenant=tenant, premises=premises, lease_from=start, lease_to=end, activity=activity,
            status=Application.Status.CONTRACTED, manager=manager,
        )
        contract = Contract.objects.create(
            number=leasing._next_contract_number(start.year),
            application=app, tenant=tenant, premises=premises, date_from=start, date_to=end,
            area_m2=premises.area_m2, rate_rub_m2=premises.rate_rub_m2,
            service_rate_rub_m2=Decimal("350.00"), deposit_rub=premises.monthly_rent,
            penalty_pct=Decimal("0.100"), created_by=manager, status=Contract.Status.ACTIVE,
        )
        version = leasing.render_contract_version(contract, manager)
        signed = timezone.make_aware(datetime.combine(start - timedelta(days=7), time(12, 0)))
        contract.approved_by = director
        contract.director_signed_at = signed
        contract.director_signature = leasing._signature(version, director, signed)
        contract.tenant_signed_at = signed + timedelta(hours=3)
        contract.tenant_signature = leasing._signature(version, tenant.user, contract.tenant_signed_at)
        contract.save()
        return contract

    def _pay(self, tenant, amount, paid_at, doc_no, purpose):
        payment = Payment.objects.create(
            tenant=tenant, payer_inn=tenant.inn, amount=Decimal(amount).quantize(Decimal("0.01")),
            paid_at=paid_at, bank_doc_no=doc_no, purpose=purpose,
        )
        allocate_payment(payment)

    def _seed(self):
        today = timezone.localdate()
        this_month = month_start(today)

        types = {name: PremisesType.objects.create(name=name) for name in ("Торговое", "Общепит", "Островное", "Склад")}
        for number, (name, rooms) in FLOORS.items():
            floor = Floor.objects.create(number=number, name=name)
            for code, type_name, area, rate, x, y, w, h in rooms:
                Premises.objects.create(
                    floor=floor, type=types[type_name], code=code, area_m2=Decimal(str(area)),
                    rate_rub_m2=Decimal(rate), plan_x=x, plan_y=y, plan_w=w, plan_h=h,
                    description=f"{type_name} помещение на {number} этаже. Отдельный вход из галереи, вентиляция, электроснабжение 15 кВт.",
                )
        Premises.objects.filter(code="2-08").update(status=Premises.Status.REPAIR)

        self._user("admin", User.Role.ADMIN, "Ирина", "Админова", is_staff=True, is_superuser=True)
        manager = self._user("manager", User.Role.MANAGER, "Олег", "Менеджеров")
        accountant = self._user("accountant", User.Role.ACCOUNTANT, "Светлана", "Счетова")
        director = self._user("director", User.Role.DIRECTOR, "Андрей", "Директоров")
        _ = accountant

        romashka = self._tenant("tenant1", "ООО «Ромашка»", "770100101", "102770010101", "г. Москва, ул. Лесная, д. 5")
        sokolova = self._tenant("tenant2", "ИП Соколова Анна Викторовна", "7702020202", "31677460012345", "г. Москва, ул. Садовая, д. 12")
        coffee = self._tenant("tenant3", "ООО «Кофе Тайм»", "770300303", "117774601234", "г. Москва, пр. Мира, д. 40")
        books = self._tenant("tenant4", "ООО «Книжный двор»", "770400404", "112774601111", "г. Москва, ул. Тверская, д. 7")

        # Действующие договоры с историей начислений.
        c1 = self._past_contract(romashka, "1-02", add_months(this_month, -4), 11, "магазин одежды", manager, director)
        c2 = self._past_contract(sokolova, "2-03", add_months(this_month, -2), 12, "салон бытовой техники", manager, director)
        c3 = self._past_contract(coffee, "3-01", add_months(this_month, -10), 11, "кофейня", manager, director)
        c4 = self._past_contract(books, "1-06", add_months(this_month, -6), 24, "книжный магазин", manager, director)
        self._past_contract(books, "3-06", add_months(this_month, -1), 12, "детский книжный клуб", manager, director)

        for contract in Contract.objects.all():
            invoice = create_deposit_invoice(contract)
            invoice.due_date = contract.date_from
            invoice.save(update_fields=["due_date"])
        month = min(c.date_from for c in Contract.objects.all())
        while month <= this_month:
            generate_invoices(month)
            month = add_months(month, 1)

        # Оплаты: «Кофе Тайм», «Книжный двор» и ИП Соколова платят вовремя, у «Ромашки» есть долг.
        doc = 100
        for contract in (c2, c3, c4):
            tenant = contract.tenant
            for invoice in Invoice.objects.filter(contract__tenant=tenant, due_date__lt=today).order_by("due_date"):
                doc += 1
                self._pay(tenant, invoice.total, min(invoice.due_date - timedelta(days=2), today), str(doc), f"Оплата по счёту {invoice.number}")
        romashka_invoices = list(Invoice.objects.filter(contract=c1).order_by("due_date", "id"))
        for invoice in romashka_invoices[:3]:  # обеспечение и два месяца
            doc += 1
            self._pay(romashka, invoice.total, invoice.due_date - timedelta(days=1), str(doc), f"Оплата по счёту {invoice.number}")
        if len(romashka_invoices) > 3:
            doc += 1
            self._pay(romashka, romashka_invoices[3].total / 2, romashka_invoices[3].due_date + timedelta(days=4), str(doc), "Частичная оплата аренды")

        # Заявки в работе — для демонстрации ролей менеджера и руководителя.
        next_month = add_months(this_month, 1)
        leasing.submit_application(sokolova, Premises.objects.get(code="1-05"), next_month, add_months(next_month, 12) - timedelta(days=1), "магазин товаров для дома", "Нужна витрина в галерею")
        app = leasing.submit_application(coffee, Premises.objects.get(code="1-О1"), next_month, add_months(next_month, 6) - timedelta(days=1), "кофейный островок")
        leasing.approve_application(app, manager)
        contract = leasing.create_contract(app, manager)
        leasing.send_for_approval(contract, manager)
        app2 = leasing.submit_application(romashka, Premises.objects.get(code="2-02"), next_month, add_months(next_month, 11) - timedelta(days=1), "магазин аксессуаров")
        leasing.approve_application(app2, manager)

        leasing.refresh_all_premises(today)
        accrue_penalties(today)
