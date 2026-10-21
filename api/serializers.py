from datetime import datetime

from rest_framework import serializers

from catalog.models import Premises
from finance.models import Invoice
from leasing.models import Application, Contract


class PremisesSerializer(serializers.ModelSerializer):
    floor = serializers.IntegerField(source="floor.number", read_only=True)
    type = serializers.CharField(source="type.name", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    monthly_rent = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)

    class Meta:
        model = Premises
        fields = ["id", "code", "floor", "type", "area_m2", "rate_rub_m2", "monthly_rent", "status", "status_display", "description"]


class ApplicationSerializer(serializers.ModelSerializer):
    premises_code = serializers.CharField(source="premises.code", read_only=True)
    tenant_name = serializers.CharField(source="tenant.legal_name", read_only=True)
    booking_expires_at = serializers.SerializerMethodField()

    class Meta:
        model = Application
        fields = [
            "id", "premises", "premises_code", "tenant_name", "lease_from", "lease_to", "activity", "comment",
            "status", "reject_reason", "booking_expires_at", "created_at",
        ]
        read_only_fields = ["status", "reject_reason", "created_at"]

    def get_booking_expires_at(self, obj) -> datetime | None:
        booking = getattr(obj, "booking", None)
        return booking.expires_at if booking and booking.is_active else None


class RejectSerializer(serializers.Serializer):
    reason = serializers.CharField()


class FromApplicationSerializer(serializers.Serializer):
    application = serializers.PrimaryKeyRelatedField(queryset=Application.objects.all())


class ContractSerializer(serializers.ModelSerializer):
    premises_code = serializers.CharField(source="premises.code", read_only=True)
    tenant_name = serializers.CharField(source="tenant.legal_name", read_only=True)
    monthly_rent = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    monthly_service = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    version_sha256 = serializers.SerializerMethodField()

    class Meta:
        model = Contract
        fields = [
            "id", "number", "application", "tenant_name", "premises", "premises_code", "date_from", "date_to",
            "area_m2", "rate_rub_m2", "service_rate_rub_m2", "monthly_rent", "monthly_service", "deposit_rub",
            "penalty_pct", "status", "director_signed_at", "director_signature", "tenant_signed_at",
            "tenant_signature", "version_sha256", "created_at",
        ]
        read_only_fields = fields

    def get_version_sha256(self, obj) -> str | None:
        version = obj.current_version
        return version.sha256 if version else None


class InvoiceSerializer(serializers.ModelSerializer):
    contract_number = serializers.CharField(source="contract.number", read_only=True)
    tenant_name = serializers.CharField(source="contract.tenant.legal_name", read_only=True)
    total = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    debt = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    is_overdue = serializers.BooleanField(read_only=True)

    class Meta:
        model = Invoice
        fields = [
            "id", "number", "contract", "contract_number", "tenant_name", "kind", "period", "rent_amount",
            "service_amount", "penalty_amount", "total", "paid_amount", "debt", "due_date", "is_overdue", "status",
        ]
        read_only_fields = fields


class PeriodSerializer(serializers.Serializer):
    period = serializers.DateField(help_text="Любая дата нужного месяца, например 2026-11-01")


class StatementUploadSerializer(serializers.Serializer):
    file = serializers.FileField()
