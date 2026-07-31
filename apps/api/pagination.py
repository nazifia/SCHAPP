from rest_framework.pagination import CursorPagination


class DefaultCursorPagination(CursorPagination):
    """Cursor, not page numbers: rows shift constantly during a school day and
    offset pagination would skip or repeat records mid-scroll on mobile."""

    page_size = 50
    max_page_size = 200
    page_size_query_param = "page_size"
    ordering = "-created_at"
