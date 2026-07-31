from .models import Domain, Tenant


def tenant_by_slug(slug: str) -> Tenant | None:
    return (
        Tenant.objects.select_related("configuration")
        .filter(slug=(slug or "").strip().lower())
        .first()
    )


def tenant_by_domain(hostname: str) -> Tenant | None:
    domain = (
        Domain.objects.select_related("tenant__configuration")
        .filter(domain=(hostname or "").strip().lower())
        .first()
    )
    # Same rule as the middleware: an unverified custom domain resolves to
    # nothing, or the branding lookup would confirm a claim the middleware
    # refuses to serve.
    return domain.tenant if domain is not None and domain.is_servable else None
