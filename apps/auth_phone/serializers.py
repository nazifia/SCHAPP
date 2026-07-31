from rest_framework import serializers

from apps.numbering.msisdn import InvalidMsisdn, normalize

from .models import OtpPurpose


class DeviceInfoSerializer(serializers.Serializer):
    device_id = serializers.CharField(max_length=128, required=False, allow_blank=True)
    name = serializers.CharField(max_length=120, required=False, allow_blank=True)
    platform = serializers.ChoiceField(
        choices=["ANDROID", "IOS", "WEB"], required=False, allow_blank=True
    )
    app_version = serializers.CharField(max_length=20, required=False, allow_blank=True)
    push_token = serializers.CharField(max_length=255, required=False, allow_blank=True)


class PushTokenSerializer(serializers.Serializer):
    """A device announcing its (new) push address."""

    device_id = serializers.CharField(max_length=128)
    push_token = serializers.CharField(max_length=255, allow_blank=True)


class PhoneField(serializers.CharField):
    """Mirrors the backend normalisation the Flutter client applies live."""

    def to_internal_value(self, data):
        raw = super().to_internal_value(data)
        try:
            return normalize(raw)
        except InvalidMsisdn as exc:
            raise serializers.ValidationError(exc.message, code=exc.code) from exc


class OtpRequestSerializer(serializers.Serializer):
    phone = PhoneField(max_length=24)
    purpose = serializers.ChoiceField(choices=OtpPurpose.choices, default=OtpPurpose.LOGIN)


class OtpRequestResponseSerializer(serializers.Serializer):
    request_id = serializers.CharField()
    expires_in = serializers.IntegerField()
    resend_after = serializers.IntegerField()
    masked_phone = serializers.CharField()


class OtpVerifySerializer(serializers.Serializer):
    request_id = serializers.UUIDField()
    code = serializers.RegexField(r"^\d{6}$", error_messages={"invalid": "Enter the 6-digit code."})
    device = DeviceInfoSerializer(required=False)


class PinLoginSerializer(serializers.Serializer):
    phone = PhoneField(max_length=24)
    pin = serializers.RegexField(r"^\d{6}$")
    device = DeviceInfoSerializer(required=False)


class PinSetSerializer(serializers.Serializer):
    pin = serializers.RegexField(r"^\d{6}$")
    confirm_pin = serializers.RegexField(r"^\d{6}$")

    def validate(self, attrs):
        if attrs["pin"] != attrs["confirm_pin"]:
            raise serializers.ValidationError({"confirm_pin": "The two PINs do not match."})
        return attrs


class RefreshSerializer(serializers.Serializer):
    refresh = serializers.CharField()


class TokenPairSerializer(serializers.Serializer):
    access = serializers.CharField()
    refresh = serializers.CharField()
    expires_in = serializers.IntegerField()
