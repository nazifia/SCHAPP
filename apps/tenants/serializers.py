from rest_framework import serializers

from .labels import labels_for
from .models import InstitutionType, Plan, Tenant, TenantConfiguration
from .services import assert_slug_available


class PlanSerializer(serializers.ModelSerializer):
    class Meta:
        model = Plan
        fields = [
            "code",
            "name",
            "price_ngn",
            "trial_days",
            "max_students",
            "max_staff",
            "modules",
        ]


class TenantSignupSerializer(serializers.Serializer):
    """Self-serve school registration."""

    name = serializers.CharField(max_length=200)
    slug = serializers.SlugField(max_length=50)
    institution_type = serializers.ChoiceField(
        choices=InstitutionType.choices, default=InstitutionType.SECONDARY
    )
    contact_name = serializers.CharField(max_length=120)
    contact_email = serializers.EmailField()
    contact_phone = serializers.CharField(max_length=20)
    plan_code = serializers.SlugField(required=False, allow_blank=True)
    # NDPR requires consent to be explicit and recorded, so it is not optional.
    accept_terms = serializers.BooleanField()

    def validate_slug(self, value: str) -> str:
        value = value.strip().lower()
        assert_slug_available(value)
        return value

    def validate_accept_terms(self, value: bool) -> bool:
        if not value:
            raise serializers.ValidationError("You must accept the terms to create an account.")
        return value

    def validate_plan_code(self, value: str) -> str:
        if value and not Plan.objects.filter(code=value, is_public=True).exists():
            raise serializers.ValidationError("Unknown plan.")
        return value


class TenantPublicSerializer(serializers.ModelSerializer):
    """What an unauthenticated client may see: enough to brand the login screen.

    Deliberately excludes contact details, gateway keys and anything else that
    would let a stranger profile a school.
    """

    branding = serializers.SerializerMethodField()
    labels = serializers.SerializerMethodField()

    class Meta:
        model = Tenant
        fields = ["slug", "name", "institution_type", "status", "branding", "labels"]

    def get_branding(self, obj: Tenant) -> dict:
        config = getattr(obj, "configuration", None)
        if config is None:
            return {}
        return {
            "primary_color": config.primary_color,
            "secondary_color": config.secondary_color,
            "logo": config.logo.url if config.logo else None,
            "motto": config.motto,
        }

    def get_labels(self, obj: Tenant) -> dict:
        config = getattr(obj, "configuration", None)
        return labels_for(obj.institution_type, config.label_overrides if config else None)


class InstitutionSettingsSerializer(serializers.ModelSerializer):
    """What a school administrator may change about their own institution.

    Deliberately not the whole model: the gateway keys and SMS credentials are
    on the same row and stay in the Django admin, because handing an app screen
    the ability to redirect a school's takings is a different risk from letting
    it change the crest.

    `label_overrides` also carries the admission/matriculation/staff number
    formats, which is why editing it is a settings action and not a branding
    one.
    """

    class Meta:
        model = TenantConfiguration
        fields = [
            "logo",
            "primary_color",
            "secondary_color",
            "report_header",
            "motto",
            "address",
            "label_overrides",
            "sms_sender_id",
            "require_nin",
        ]


class TenantSignupResultSerializer(serializers.Serializer):
    slug = serializers.SlugField()
    name = serializers.CharField()
    status = serializers.CharField()
    domain = serializers.CharField()
