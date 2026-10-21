"""Тесты ключевого пути и ограничений целостности. Запуск: python manage.py test"""
import threading
from datetime import date, timedelta
from decimal import Decimal

from django.db import IntegrityError, connection, transaction
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import Tenant, User
from accounts.validators import complete_inn, complete_ogrn, is_valid_inn, is_valid_ogrn
from catalog.models import Floor, Premises, PremisesType
from finance import services as finance
from finance.models import Invoice, Payment
from leasing import services as leasing
from leasing.models import Application, Booking, Contract


def next_month_start(today=None):
    today = today or timezone.localdate()
    return (today.replace(day=1) + timedelta(days=32)).replace(day=1)


class Fixtures:
    def make_world(self):
        self.floor = Floor.objects.create(number=1)
        self.ptype = PremisesType.objects.create(name="Торговое")
        self.premises = Premises.objects.create(
            floor=self.floor, type=self.ptype, code="1-01", area_m2=Decimal("100.00"), rate_rub_m2=Decimal("3000.00")
        )
        self.other = Premises.objects.create(
            floor=self.floor, type=self.ptype, code="1-02", area_m2=Decimal("50.00"), rate_rub_m2=Decimal("4000.00")
        )
        self.manager = User.objects.create_user("manager", password="x", role=User.Role.MANAGER)
        self.director = User.objects.create_user("director", password="x", role=User.Role.DIRECTOR)
        self.accountant = User.objects.create_user("accountant", password="x", role=User.Role.ACCOUNTANT)
        self.t1 = self.make_tenant("t1", "770100101")
        self.t2 = self.make_tenant("t2", "770300303")
        self.start = next_month_start()
        self.end = self.start.replace(year=self.start.year + 1) - timedelta(days=1)

    def make_tenant(self, username, inn_base):
        user = User.objects.create_user(username, password="x", role=User.Role.TENANT)
        return Tenant.objects.create(
            user=user, legal_name=f"ООО {username}", inn=complete_inn(inn_base), ogrn=complete_ogrn("102770010101")
        )

    def full_flow(self, tenant=None, premises=None):
        tenant = tenant or self.t1
        premises = premises or self.premises
        app = leasing.submit_application(tenant, premises, self.start, self.end, "магазин")
        leasing.approve_application(app, self.manager)
        contract = leasing.create_contract(app, self.manager)
        leasing.send_for_approval(contract, self.manager)
        leasing.approve_contract(contract, self.director)
        return leasing.sign_contract(contract, tenant.user)


class ValidatorTests(TestCase):
    def test_known_inn(self):
        self.assertTrue(is_valid_inn("7707083893"))  # ПАО Сбербанк
        self.assertFalse(is_valid_inn("7707083894"))
        self.assertFalse(is_valid_inn("12345"))

    def test_generated_inn_and_ogrn(self):
        self.assertTrue(is_valid_inn(complete_inn("771234567")))
        self.assertTrue(is_valid_inn(complete_inn("7712345678")))
        self.assertTrue(is_valid_ogrn(complete_ogrn("102770010101")))
        self.assertTrue(is_valid_ogrn(complete_ogrn("31677460012345")))
        self.assertTrue(is_valid_ogrn("1027700132195"))


class DatabaseConstraintTests(Fixtures, TestCase):
    """Проверяем, что двойную сдачу блокирует сама PostgreSQL, в обход сервисов."""

    def setUp(self):
        self.make_world()

    def _app(self, tenant):
        return Application.objects.create(
            tenant=tenant, premises=self.premises, lease_from=self.start, lease_to=self.end, activity="x"
        )

    def test_overlapping_active_bookings_rejected(self):
        expires = timezone.now() + timedelta(days=5)
        Booking.objects.create(application=self._app(self.t1), premises=self.premises,
                               lease_from=self.start, lease_to=self.end, expires_at=expires)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Booking.objects.create(application=self._app(self.t2), premises=self.premises,
                                   lease_from=self.end, lease_to=self.end + timedelta(days=60), expires_at=expires)

    def test_inactive_booking_does_not_block(self):
        expires = timezone.now() + timedelta(days=5)
        Booking.objects.create(application=self._app(self.t1), premises=self.premises,
                               lease_from=self.start, lease_to=self.end, expires_at=expires, is_active=False)
        Booking.objects.create(application=self._app(self.t2), premises=self.premises,
                               lease_from=self.start, lease_to=self.end, expires_at=expires)

    def test_overlapping_contracts_rejected(self):
        common = dict(premises=self.premises, area_m2=100, rate_rub_m2=1, service_rate_rub_m2=1,
                      deposit_rub=1, penalty_pct=Decimal("0.1"), created_by=self.manager, status=Contract.Status.ACTIVE)
        Contract.objects.create(number="A-1", tenant=self.t1, date_from=self.start, date_to=self.end, **common)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Contract.objects.create(number="A-2", tenant=self.t2, date_from=self.start + timedelta(days=30),
                                    date_to=self.end + timedelta(days=30), **common)
        # Отменённый договор период не занимает.
        Contract.objects.create(number="A-3", tenant=self.t2, date_from=self.start, date_to=self.end,
                                **{**common, "status": Contract.Status.CANCELLED})


class LeasingFlowTests(Fixtures, TestCase):
    def setUp(self):
        self.make_world()

    def test_full_flow(self):
        contract = self.full_flow()
        self.premises.refresh_from_db()
        self.assertEqual(contract.status, Contract.Status.ACTIVE)
        self.assertEqual(len(contract.director_signature), 64)
        self.assertEqual(len(contract.tenant_signature), 64)
        self.assertFalse(Booking.objects.get(application=contract.application).is_active)
        # Договор начинается в следующем месяце — помещение пока «забронировано», не «сдано».
        self.assertEqual(self.premises.status, Premises.Status.BOOKED)
        leasing.refresh_premises_status(self.premises, today=self.start)
        self.assertEqual(self.premises.status, Premises.Status.LEASED)
        deposit = Invoice.objects.get(contract=contract, kind=Invoice.Kind.DEPOSIT)
        self.assertEqual(deposit.rent_amount, Decimal("300000.00"))

    def test_second_application_for_same_period_conflicts(self):
        leasing.submit_application(self.t1, self.premises, self.start, self.end, "магазин")
        with self.assertRaises(leasing.PremisesUnavailable):
            leasing.submit_application(self.t2, self.premises, self.start + timedelta(days=40), self.end, "кафе")
        self.assertEqual(Application.objects.count(), 1)  # неудачная заявка откатилась целиком

    def test_non_overlapping_period_allowed(self):
        leasing.submit_application(self.t1, self.premises, self.start, self.end, "магазин")
        later = self.end + timedelta(days=1)
        leasing.submit_application(self.t2, self.premises, later, later + timedelta(days=90), "кафе")

    def test_contract_blocks_after_booking_released(self):
        self.full_flow()
        with self.assertRaises(leasing.PremisesUnavailable):
            leasing.submit_application(self.t2, self.premises, self.start, self.end, "кафе")

    def test_reject_releases_booking(self):
        app = leasing.submit_application(self.t1, self.premises, self.start, self.end, "магазин")
        leasing.reject_application(app, self.manager, "Профиль не подходит")
        leasing.submit_application(self.t2, self.premises, self.start, self.end, "кафе")

    def test_validation_rules(self):
        with self.assertRaises(leasing.LeasingError):
            leasing.submit_application(self.t1, self.premises, date(2000, 1, 1), self.end, "магазин")
        with self.assertRaises(leasing.LeasingError):
            leasing.submit_application(self.t1, self.premises, self.start, self.start + timedelta(days=10), "магазин")
        self.premises.status = Premises.Status.REPAIR
        self.premises.save()
        with self.assertRaises(leasing.PremisesUnavailable):
            leasing.submit_application(self.t1, self.premises, self.start, self.end, "магазин")

    def test_wrong_status_transitions(self):
        app = leasing.submit_application(self.t1, self.premises, self.start, self.end, "магазин")
        with self.assertRaises(leasing.LeasingError):
            leasing.create_contract(app, self.manager)  # заявка ещё не одобрена
        leasing.approve_application(app, self.manager)
        contract = leasing.create_contract(app, self.manager)
        with self.assertRaises(leasing.LeasingError):
            leasing.approve_contract(contract, self.director)  # не отправлен на согласование
        leasing.send_for_approval(contract, self.manager)
        leasing.approve_contract(contract, self.director)
        with self.assertRaises(leasing.LeasingError):
            leasing.sign_contract(contract, self.t2.user)  # чужой арендатор

    def test_return_for_revision_and_new_version(self):
        app = leasing.submit_application(self.t1, self.premises, self.start, self.end, "магазин")
        leasing.approve_application(app, self.manager)
        contract = leasing.create_contract(app, self.manager)
        leasing.send_for_approval(contract, self.manager)
        leasing.return_for_revision(contract, self.director, "Снизить ставку")
        leasing.update_draft_terms(contract, self.manager, rate_rub_m2=Decimal("2800.00"))
        contract.refresh_from_db()
        self.assertEqual(contract.status, Contract.Status.DRAFT)
        self.assertEqual(contract.versions.count(), 2)
        self.assertIn("2 800,00", contract.current_version.text_html)

    def test_expired_booking_cancels_contract(self):
        app = leasing.submit_application(self.t1, self.premises, self.start, self.end, "магазин")
        leasing.approve_application(app, self.manager)
        contract = leasing.create_contract(app, self.manager)
        leasing.send_for_approval(contract, self.manager)
        leasing.approve_contract(contract, self.director)
        removed = leasing.expire_bookings(now=timezone.now() + timedelta(days=30))
        self.assertEqual(removed, 1)
        contract.refresh_from_db()
        app.refresh_from_db()
        self.premises.refresh_from_db()
        self.assertEqual(contract.status, Contract.Status.CANCELLED)
        self.assertEqual(app.status, Application.Status.EXPIRED)
        self.assertEqual(self.premises.status, Premises.Status.FREE)
        with self.assertRaises(leasing.LeasingError):
            leasing.sign_contract(contract, self.t1.user)

    def test_business_days(self):
        friday = timezone.make_aware(timezone.datetime(2026, 10, 23, 10, 0))
        self.assertEqual(leasing.add_business_days(friday, 5).date(), date(2026, 10, 30))


class FinanceTests(Fixtures, TestCase):
    def setUp(self):
        self.make_world()
        self.contract = self.full_flow()

    def test_monthly_invoice_and_idempotency(self):
        created = finance.generate_invoices(self.start)
        self.assertEqual(len(created), 1)
        inv = created[0]
        self.assertEqual(inv.rent_amount, Decimal("300000.00"))
        self.assertEqual(inv.service_amount, Decimal("35000.00"))
        self.assertEqual(inv.due_date, self.start.replace(day=10))
        self.assertEqual(finance.generate_invoices(self.start), [])

    def test_partial_month_is_prorated(self):
        Contract.objects.filter(pk=self.contract.pk).update(date_to=self.end.replace(day=15))
        last = finance.generate_invoices(self.end)[0]
        days = (finance.month_end(self.end) - finance.month_start(self.end)).days + 1
        expected = (Decimal("300000") * Decimal(15) / Decimal(days)).quantize(Decimal("0.01"))
        self.assertEqual(last.rent_amount, expected)

    def test_import_allocates_fifo_and_skips_duplicates(self):
        rent = finance.generate_invoices(self.start)[0]
        deposit = Invoice.objects.get(contract=self.contract, kind=Invoice.Kind.DEPOSIT)
        csv = (
            "дата;инн;сумма;номер;назначение\n"
            f"01.11.2026;{self.t1.inn};400000,00;501;обеспечение и аренда\n"
            f"01.11.2026;{self.t1.inn};400000,00;501;повтор\n"
            "02.11.2026;1234567894;1000,00;502;чужой платёж\n"
            "не дата;;;;\n"
        )
        report = finance.import_statement(csv)
        self.assertEqual((len(report.matched), len(report.duplicates), len(report.unmatched), len(report.errors)), (1, 1, 1, 1))
        deposit.refresh_from_db()
        rent.refresh_from_db()
        self.assertEqual(deposit.status, Invoice.Status.PAID)
        self.assertEqual(rent.paid_amount, Decimal("100000.00"))
        self.assertEqual(rent.status, Invoice.Status.PARTIALLY_PAID)

    def test_advance_applied_to_next_invoice(self):
        deposit = Invoice.objects.get(contract=self.contract, kind=Invoice.Kind.DEPOSIT)
        payment = Payment.objects.create(tenant=self.t1, payer_inn=self.t1.inn, amount=deposit.total + 50000,
                                         paid_at=timezone.localdate(), bank_doc_no="900")
        self.assertEqual(finance.allocate_payment(payment), Decimal("50000.00"))
        rent = finance.generate_invoices(self.start)[0]
        rent.refresh_from_db()
        self.assertEqual(rent.paid_amount, Decimal("50000.00"))

    def test_penalty_accrual(self):
        rent = finance.generate_invoices(self.start)[0]
        as_of = rent.due_date + timedelta(days=10)
        finance.accrue_penalties(as_of)
        rent.refresh_from_db()
        # 335 000 × 0,1% × 10 дней
        self.assertEqual(rent.penalty_amount, Decimal("3350.00"))
        finance.accrue_penalties(as_of)  # повторный запуск не удваивает пени
        rent.refresh_from_db()
        self.assertEqual(rent.penalty_amount, Decimal("3350.00"))


class ConcurrencyTests(Fixtures, TransactionTestCase):
    """Две одновременные заявки на одно помещение: успешна ровно одна."""

    def setUp(self):
        self.make_world()

    def test_parallel_applications(self):
        results, barrier = [], threading.Barrier(2)

        def worker(tenant):
            try:
                barrier.wait()
                leasing.submit_application(tenant, self.premises, self.start, self.end, "магазин")
                results.append("ok")
            except leasing.PremisesUnavailable:
                results.append("conflict")
            finally:
                connection.close()

        threads = [threading.Thread(target=worker, args=(t,)) for t in (self.t1, self.t2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sorted(results), ["conflict", "ok"])
        self.assertEqual(Booking.objects.filter(is_active=True).count(), 1)


class WebAndApiTests(Fixtures, TestCase):
    def setUp(self):
        self.make_world()

    def test_catalog_pages_public(self):
        self.assertEqual(self.client.get(reverse("catalog:list")).status_code, 200)
        self.assertEqual(self.client.get(reverse("catalog:detail", args=[self.premises.pk])).status_code, 200)

    def test_tenant_submits_via_web(self):
        self.client.force_login(self.t1.user)
        resp = self.client.post(reverse("catalog:detail", args=[self.premises.pk]), {
            "lease_from": self.start.isoformat(), "lease_to": self.end.isoformat(), "activity": "магазин",
        })
        self.assertRedirects(resp, reverse("leasing:cabinet"))
        self.assertEqual(self.client.get(reverse("leasing:cabinet")).status_code, 200)

    def test_role_pages(self):
        self.full_flow()
        finance.generate_invoices(self.start)
        for user, url in [
            (self.manager, reverse("leasing:applications")),
            (self.manager, reverse("leasing:contracts")),
            (self.accountant, reverse("finance:home")),
            (self.director, reverse("dashboard")),
            (self.t1.user, reverse("leasing:contract", args=[Contract.objects.get().pk])),
        ]:
            self.client.force_login(user)
            self.assertEqual(self.client.get(url).status_code, 200, url)
        self.client.force_login(self.t1.user)
        self.assertEqual(self.client.get(reverse("finance:home")).status_code, 403)

    def test_registration_validates_inn(self):
        data = {
            "username": "newco", "email": "a@b.ru", "password1": "Strong-pass-123", "password2": "Strong-pass-123",
            "legal_name": "ООО Новая", "inn": "7707083894", "ogrn": "1027700132195", "agree": "on",
        }
        resp = self.client.post(reverse("register"), data)
        self.assertContains(resp, "Некорректный ИНН")
        data["inn"] = complete_inn("770111111")
        resp = self.client.post(reverse("register"), data)
        self.assertRedirects(resp, reverse("catalog:list"))
        self.assertTrue(Tenant.objects.filter(user__username="newco").exists())

    def test_api_flow_with_conflict(self):
        api = APIClient()
        self.assertEqual(api.get("/api/premises/").status_code, 200)
        api.force_authenticate(self.t1.user)
        body = {"premises": self.premises.pk, "lease_from": self.start, "lease_to": self.end, "activity": "магазин"}
        first = api.post("/api/applications/", body, format="json")
        self.assertEqual(first.status_code, 201, first.content)
        api.force_authenticate(self.t2.user)
        self.assertEqual(api.post("/api/applications/", body, format="json").status_code, 409)

        api.force_authenticate(self.manager)
        app_id = first.data["id"]
        self.assertEqual(api.post(f"/api/applications/{app_id}/approve/").status_code, 200)
        created = api.post("/api/contracts/from-application/", {"application": app_id}, format="json")
        self.assertEqual(created.status_code, 201, created.content)
        cid = created.data["id"]
        self.assertEqual(api.post(f"/api/contracts/{cid}/send-for-approval/").status_code, 200)
        api.force_authenticate(self.director)
        self.assertEqual(api.post(f"/api/contracts/{cid}/approve/").status_code, 200)
        api.force_authenticate(self.t1.user)
        signed = api.post(f"/api/contracts/{cid}/sign/")
        self.assertEqual(signed.data["status"], "active")
        self.assertEqual(api.get("/api/invoices/").data["count"], 1)

    def test_api_permissions(self):
        api = APIClient()
        api.force_authenticate(self.t1.user)
        self.assertEqual(api.post("/api/invoices/generate/", {"period": self.start}).status_code, 403)
        self.assertEqual(api.get("/api/reports/occupancy/").status_code, 403)
        api.force_authenticate(self.director)
        self.assertEqual(api.get("/api/reports/occupancy/").status_code, 200)


class SeedTests(TestCase):
    def test_seed_runs(self):
        from django.core.management import call_command

        call_command("seed_demo", verbosity=0)
        self.assertEqual(Premises.objects.count(), 30)
        self.assertTrue(Contract.objects.filter(status=Contract.Status.ACTIVE).exists())
        self.assertTrue(Contract.objects.filter(status=Contract.Status.APPROVAL).exists())
        self.assertTrue(Invoice.objects.filter(status=Invoice.Status.PAID).exists())
        call_command("run_scheduler", "--once", verbosity=0)
