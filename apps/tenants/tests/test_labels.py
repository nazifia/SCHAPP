from apps.tenants.labels import labels_for
from apps.tenants.models import InstitutionType


def test_secondary_and_tertiary_use_different_terminology():
    secondary = labels_for(InstitutionType.SECONDARY)
    tertiary = labels_for(InstitutionType.TERTIARY)

    assert secondary["TERM"] == "Term"
    assert tertiary["TERM"] == "Semester"
    assert secondary["SUBJECT"] == "Subject"
    assert tertiary["SUBJECT"] == "Course"
    assert secondary["CLASS"] == "Class"
    assert tertiary["CLASS"] == "Level"


def test_both_types_define_the_same_label_keys():
    # A missing key would surface as a KeyError in a PDF template at 2am.
    assert (
        labels_for(InstitutionType.SECONDARY).keys() == labels_for(InstitutionType.TERTIARY).keys()
    )


def test_tenant_overrides_win():
    labels = labels_for(InstitutionType.SECONDARY, {"TERM": "Quarter"})
    assert labels["TERM"] == "Quarter"
    assert labels["SUBJECT"] == "Subject"


def test_unknown_type_falls_back_to_secondary():
    assert labels_for("SOMETHING_ELSE")["TERM"] == "Term"
