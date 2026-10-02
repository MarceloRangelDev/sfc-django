from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response


class PaginacaoPadrao(PageNumberPagination):
    """?pagina=2&por_pagina=50 (máximo 100). O resultado vai em
    data = {"itens": [...], "paginacao": {...}} dentro do envelope padrão."""

    page_query_param = "pagina"
    page_size_query_param = "por_pagina"
    page_size = 25
    max_page_size = 100

    def get_paginated_response(self, data):
        return Response(
            {
                "itens": data,
                "paginacao": {
                    "pagina": self.page.number,
                    "por_pagina": self.get_page_size(self.request),
                    "total": self.page.paginator.count,
                    "total_paginas": self.page.paginator.num_pages,
                },
            }
        )

    def get_paginated_response_schema(self, schema):
        return {
            "type": "object",
            "properties": {
                "itens": schema,
                "paginacao": {
                    "type": "object",
                    "properties": {
                        "pagina": {"type": "integer"},
                        "por_pagina": {"type": "integer"},
                        "total": {"type": "integer"},
                        "total_paginas": {"type": "integer"},
                    },
                },
            },
        }
