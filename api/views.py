"""REST API. Вся логика — в сервисах leasing/finance; здесь только HTTP-обвязка."""
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from catalog.selectors import filter_premises
from core import reports
from finance import services as finance
from finance.models import Invoice
from leasing import services as leasing
from leasing.models import Application, Contract

from . import serializers as s
from .permissions import IsAccountant, IsDirector, IsManager, IsStaffRole, IsTenant


def _error(exc: leasing.LeasingError):
    code = status.HTTP_409_CONFLICT if isinstance(exc, leasing.PremisesUnavailable) else status.HTTP_400_BAD_REQUEST
    return Response({"detail": str(exc)}, status=code)


class PremisesViewSet(viewsets.ReadOnlyModelViewSet):
    """Каталог. Фильтры: floor, type, area_min, area_max, price_max, period_from, period_to, only_free."""

    serializer_class = s.PremisesSerializer
    permission_classes = [AllowAny]

    def get_queryset(self):
        qs, _ = filter_premises(self.request.query_params)
        return qs


class ApplicationViewSet(mixins.CreateModelMixin, viewsets.ReadOnlyModelViewSet):
    serializer_class = s.ApplicationSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):  # генерация схемы OpenAPI
            return Application.objects.none()
        qs = Application.objects.select_related("tenant", "premises", "booking")
        user = self.request.user
        if hasattr(user, "tenant"):
            return qs.filter(tenant=user.tenant)
        if user.has_role(user.Role.MANAGER, user.Role.DIRECTOR):
            return qs
        return qs.none()

    def get_permissions(self):
        if self.action == "create":
            return [IsTenant()]
        if self.action in ("approve", "reject"):
            return [IsManager()]
        return [IsAuthenticated()]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            application = leasing.submit_application(
                request.user.tenant, data["premises"], data["lease_from"], data["lease_to"],
                data["activity"], data.get("comment", ""),
            )
        except leasing.LeasingError as exc:
            return _error(exc)
        return Response(self.get_serializer(application).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=None)
    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        try:
            application = leasing.approve_application(self.get_object(), request.user)
        except leasing.LeasingError as exc:
            return _error(exc)
        return Response(self.get_serializer(application).data)

    @extend_schema(request=s.RejectSerializer)
    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        body = s.RejectSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        try:
            application = leasing.reject_application(self.get_object(), request.user, body.validated_data["reason"])
        except leasing.LeasingError as exc:
            return _error(exc)
        return Response(self.get_serializer(application).data)


class ContractViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = s.ContractSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):  # генерация схемы OpenAPI
            return Contract.objects.none()
        qs = Contract.objects.select_related("tenant", "premises")
        user = self.request.user
        if hasattr(user, "tenant"):
            return qs.filter(tenant=user.tenant).exclude(status__in=[Contract.Status.DRAFT, Contract.Status.APPROVAL])
        if user.has_role(user.Role.MANAGER, user.Role.DIRECTOR, user.Role.ACCOUNTANT):
            return qs
        return qs.none()

    def get_permissions(self):
        perms = {
            "from_application": [IsManager()],
            "send_for_approval": [IsManager()],
            "approve": [IsDirector()],
            "sign": [IsTenant()],
        }
        return perms.get(self.action, [IsAuthenticated()])

    def _run(self, func, *args):
        try:
            contract = func(*args)
        except leasing.LeasingError as exc:
            return _error(exc)
        return Response(self.get_serializer(contract).data)

    @extend_schema(request=s.FromApplicationSerializer, responses=s.ContractSerializer)
    @action(detail=False, methods=["post"], url_path="from-application")
    def from_application(self, request):
        body = s.FromApplicationSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        try:
            contract = leasing.create_contract(body.validated_data["application"], request.user)
        except leasing.LeasingError as exc:
            return _error(exc)
        return Response(self.get_serializer(contract).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=None)
    @action(detail=True, methods=["post"], url_path="send-for-approval")
    def send_for_approval(self, request, pk=None):
        return self._run(leasing.send_for_approval, self.get_object(), request.user)

    @extend_schema(request=None)
    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        return self._run(leasing.approve_contract, self.get_object(), request.user)

    @extend_schema(request=None)
    @action(detail=True, methods=["post"])
    def sign(self, request, pk=None):
        return self._run(leasing.sign_contract, self.get_object(), request.user)


class InvoiceViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = s.InvoiceSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):  # генерация схемы OpenAPI
            return Invoice.objects.none()
        qs = Invoice.objects.select_related("contract", "contract__tenant")
        user = self.request.user
        if hasattr(user, "tenant"):
            return qs.filter(contract__tenant=user.tenant)
        if user.has_role(user.Role.ACCOUNTANT, user.Role.DIRECTOR, user.Role.MANAGER):
            return qs
        return qs.none()

    def get_permissions(self):
        if self.action == "generate":
            return [IsAccountant()]
        return [IsAuthenticated()]

    @extend_schema(request=s.PeriodSerializer, responses=s.InvoiceSerializer(many=True))
    @action(detail=False, methods=["post"])
    def generate(self, request):
        body = s.PeriodSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        created = finance.generate_invoices(body.validated_data["period"])
        return Response(self.get_serializer(created, many=True).data, status=status.HTTP_201_CREATED)


@extend_schema(request={"multipart/form-data": s.StatementUploadSerializer}, responses=OpenApiTypes.OBJECT)
@api_view(["POST"])
@permission_classes([IsAccountant])
def payments_import(request):
    body = s.StatementUploadSerializer(data=request.data)
    body.is_valid(raise_exception=True)
    raw = body.validated_data["file"].read()
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        content = raw.decode("cp1251")
    report = finance.import_statement(content)
    return Response({
        "matched": [{"line": m["line"], "payment_id": m["payment"].pk, "advance": str(m["advance"])} for m in report.matched],
        "unmatched": [{"line": m["line"], "payment_id": m["payment"].pk} for m in report.unmatched],
        "duplicates": report.duplicates,
        "errors": report.errors,
    })


@extend_schema(responses=OpenApiTypes.OBJECT)
@api_view(["GET"])
@permission_classes([IsStaffRole])
def report_occupancy(request):
    rows, overall = reports.occupancy_by_floor()
    return Response({
        "floors": [
            {"floor": r["floor"].number, "total_m2": r["total"], "leased_m2": r["leased"],
             "booked_m2": r["booked"], "leased_pct": r["pct"]}
            for r in rows
        ],
        "total_m2": overall["total"], "leased_m2": overall["leased"], "leased_pct": overall["pct"],
    })


@extend_schema(responses=OpenApiTypes.OBJECT)
@api_view(["GET"])
@permission_classes([IsStaffRole])
def report_debt(request):
    today = timezone.localdate()
    return Response({"as_of": today, "debtors": reports.debtors(today, limit=100)})
