from apps.common.fields import EncryptedTextField, mask_tail


def test_roundtrip_without_touching_the_database():
    field = EncryptedTextField()
    stored = field.get_prep_value("sk_live_paystack_secret")

    assert stored.startswith("enc:")
    assert "paystack" not in stored  # ciphertext, not obfuscation
    assert field.from_db_value(stored, None, None) == "sk_live_paystack_secret"


def test_same_plaintext_encrypts_differently_each_time():
    field = EncryptedTextField()
    assert field.get_prep_value("abc") != field.get_prep_value("abc")


def test_blank_values_pass_through():
    field = EncryptedTextField()
    assert field.get_prep_value("") == ""
    assert field.get_prep_value(None) is None
    assert field.from_db_value(None, None, None) is None


def test_legacy_cleartext_is_returned_as_is():
    assert EncryptedTextField().from_db_value("plain-old-value", None, None) == "plain-old-value"


def test_a_value_the_key_cannot_read_is_empty_but_not_plain_empty():
    """Wrong key: reads as unconfigured, so nothing crashes — but the field
    can still tell it apart from a genuinely blank column."""
    from cryptography.fernet import Fernet

    from apps.common.fields import UNREADABLE

    field = EncryptedTextField()
    foreign = "enc:" + Fernet(Fernet.generate_key()).encrypt(b"someone-elses").decode()

    value = field.from_db_value(foreign, None, None)
    assert value == ""
    assert not value
    assert value is UNREADABLE


def test_an_unreadable_secret_is_never_written_back():
    """The data-loss path: one edit to an unrelated field on the same row and
    `Model.save()` would blank every secret on it, for good."""
    import pytest
    from django.core.exceptions import ImproperlyConfigured

    from apps.common.fields import UNREADABLE
    from apps.tenants.models import TenantConfiguration

    field = EncryptedTextField()
    field.set_attributes_from_name("paystack_secret_key")
    config = TenantConfiguration(paystack_secret_key=UNREADABLE)

    with pytest.raises(ImproperlyConfigured, match="FIELD_ENCRYPTION_KEY"):
        field.pre_save(config, add=False)


def test_setting_a_fresh_value_clears_the_refusal():
    """Fixing it by supplying a new secret has to work — the guard is about
    accidental blanking, not about locking the row."""
    from apps.tenants.models import TenantConfiguration

    field = EncryptedTextField()
    field.set_attributes_from_name("paystack_secret_key")
    config = TenantConfiguration(paystack_secret_key="sk_live_new")

    assert field.pre_save(config, add=False) == "sk_live_new"


def test_an_ordinary_blank_still_saves():
    from apps.tenants.models import TenantConfiguration

    field = EncryptedTextField()
    field.set_attributes_from_name("paystack_secret_key")
    assert field.pre_save(TenantConfiguration(paystack_secret_key=""), add=False) == ""


def test_mask_tail_keeps_only_the_last_four():
    assert mask_tail("12345678912") == "•••••••8912"
    assert mask_tail("") == ""
    assert mask_tail(None) == ""
