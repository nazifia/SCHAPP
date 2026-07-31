"""`/api/v1/public/verify/<slug>/<token>/` — is this document real?

Public and unauthenticated, because the person checking is an employer, a
university admissions officer or a gateman, and none of them have an account
here. The token off the document *is* the credential: it cannot be guessed and
it cannot be edited, so answering it reveals nothing that the document in the
holder's hand does not already say.

Deliberately thin on detail. Name, number, class and status confirm the sheet;
date of birth, phone number and address are not part of "is this genuine" and
would turn a verification page into a lookup service.

It lives in `apps.api` with the reporting views and for the same reason: it
owns no data and reads two other modules.
"""

from django.views.decorators.csrf import csrf_exempt
from drf_spectacular.utils import OpenApiParameter, OpenApiTypes, extend_schema
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common import verification
from apps.tenants.db import schema_context
from apps.tenants.models import Tenant


def _unverified(reason: str) -> Response:
    # 200, not 404: "we could not verify this" is the answer to the question,
    # and a scanner that shows a browser error page has told the holder
    # nothing. The body is what distinguishes the two outcomes.
    return Response({"verified": False, "reason": reason})


class DocumentVerificationView(APIView):
    permission_classes = [AllowAny]
    authentication_classes: list = []

    #: The only public, unauthenticated endpoint here that was not throttled,
    #: and it is the one that opens a tenant database and runs three queries
    #: per call. The token is unguessable, so this is not about enumeration —
    #: it is that anyone holding one printed card could otherwise replay it as
    #: fast as they liked. The rate is deliberately generous: a university
    #: admissions office checking a stack of transcripts is the intended user.
    throttle_scope = "document_verify"

    @extend_schema(
        parameters=[
            OpenApiParameter("tenant_slug", OpenApiTypes.STR, OpenApiParameter.PATH),
            OpenApiParameter("token", OpenApiTypes.STR, OpenApiParameter.PATH),
        ],
        responses={200: None},
        description="Check a printed transcript or ID card against its signature.",
    )
    def get(self, request, tenant_slug: str, token: str):
        tenant = Tenant.objects.filter(slug=tenant_slug).first()
        # `is_servable` rather than "exists": a suspended school's database is
        # not one we answer questions out of, and the same rule gates every
        # other request into that tenant.
        if tenant is None or not tenant.is_servable:
            return _unverified("UNKNOWN_INSTITUTION")

        try:
            claim = verification.unsign(token, tenant_slug=tenant_slug)
        except verification.InvalidToken:
            return _unverified("INVALID_SIGNATURE")

        with schema_context(tenant.schema_name):
            document = self._describe(claim)

        if document is None:
            # Signed by us, but the record is gone — a student expunged after a
            # data-protection request, or a token minted against a test row.
            return _unverified("NOT_ON_RECORD")

        return Response(
            {
                "verified": True,
                "institution": tenant.name,
                "document": claim["kind"],
                "issued_on": claim["issued_on"],
                **document,
            }
        )

    def _describe(self, claim: dict) -> dict | None:
        from apps.assessment.models import TermResult
        from apps.people.models import Student

        student = Student.objects.alive().filter(pk=claim["id"]).first()
        if student is None:
            return None

        described = {
            "name": student.full_name,
            "number": student.display_number,
            "status": student.get_status_display(),
            "class": str(student.current_arm or student.current_level or ""),
            "programme": str(student.programme or ""),
        }
        if claim["kind"] == verification.TRANSCRIPT:
            # The summary an employer is actually checking against the sheet.
            latest = (
                TermResult.objects.filter(enrolment__student=student)
                .order_by("-term__session__start_date", "-term__index")
                .first()
            )
            described["cgpa"] = str(latest.cgpa) if latest and latest.cgpa is not None else ""
            described["terms_on_record"] = TermResult.objects.filter(
                enrolment__student=student
            ).count()
        return described


verify_document = csrf_exempt(DocumentVerificationView.as_view())
