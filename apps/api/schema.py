"""Pós-processamento do schema OpenAPI (Swagger).

O envelope {success, message, data} é montado em runtime por
apps/api/renderers.py; os serializers só descrevem `data`. Este hook (que
roda ao gerar o schema, a partir do código) embrulha cada resposta de
sucesso no mesmo envelope e acrescenta as respostas de erro padrão — assim
a documentação é gerada do código, sem texto duplicado à mão.
"""

ERRO_PADRAO = {
    "type": "object",
    "properties": {
        "success": {"type": "boolean", "example": False},
        "message": {"type": "string", "example": "Requisição inválida."},
        "errors": {
            "type": "object",
            "additionalProperties": True,
            "example": {"campo": ["Mensagem de erro."]},
        },
    },
    "required": ["success", "message", "errors"],
}

DESCRICAO_ERROS = {
    "400": "Requisição inválida (erros de validação em `errors`).",
    "401": "Não autenticado: token ausente, inválido ou expirado.",
    "403": "Sem permissão (perfil, módulo ou cliente).",
    "404": "Registro não encontrado (ou de outro cliente).",
    "409": "Conflito: registro duplicado.",
    "422": "Viola uma regra de negócio.",
    "429": "Limite de requisições excedido.",
}

PUBLICAS = ("/api/v1/auth/login/", "/api/v1/auth/register/", "/api/v1/auth/password/forgot/", "/api/v1/auth/password/reset/")


def _resposta_erro(codigo):
    return {
        "description": DESCRICAO_ERROS[codigo],
        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErroPadrao"}}},
    }


def envelopar_respostas(result, generator, request, public):
    result.setdefault("components", {}).setdefault("schemas", {})["ErroPadrao"] = ERRO_PADRAO

    for caminho, operacoes in result.get("paths", {}).items():
        for metodo, operacao in operacoes.items():
            if metodo not in ("get", "post", "put", "patch", "delete"):
                continue
            respostas = operacao.setdefault("responses", {})

            for codigo, resposta in list(respostas.items()):
                conteudo = (resposta.get("content") or {}).get("application/json")
                if codigo.startswith("2") and conteudo:
                    conteudo["schema"] = {
                        "type": "object",
                        "properties": {
                            "success": {"type": "boolean", "example": True},
                            "message": {"type": "string", "example": "Operação realizada com sucesso."},
                            "data": conteudo.get("schema", {}),
                        },
                        "required": ["success", "message", "data"],
                    }

            erros = []
            if metodo in ("post", "put", "patch"):
                erros += ["400", "409", "422"]
            if caminho not in PUBLICAS:
                erros += ["401", "403"]
            else:
                erros += ["429"]
            if "{" in caminho:
                erros.append("404")
            for codigo in erros:
                respostas.setdefault(codigo, _resposta_erro(codigo))

            if caminho in PUBLICAS:
                operacao["security"] = []  # não exige token
    return result
