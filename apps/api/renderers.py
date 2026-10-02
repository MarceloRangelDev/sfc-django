from rest_framework.renderers import JSONRenderer

MENSAGENS_PADRAO = {
    200: "Operação realizada com sucesso.",
    201: "Registro criado com sucesso.",
}


class EnvelopeRenderer(JSONRenderer):
    """Padroniza TODA resposta da API:

        sucesso: {"success": true,  "message": "...", "data": ...}
        erro:    {"success": false, "message": "...", "errors": {...}}

    As views só devolvem os dados; o envelope é montado aqui, num único
    lugar. A mensagem pode ser personalizada com `resposta.message = "..."`
    (ver apps/api/views.py::resposta). Erros já chegam prontos de
    apps/api/exceptions.py. 204 (No Content) não tem corpo.
    """

    def render(self, data, accepted_media_type=None, renderer_context=None):
        resposta = (renderer_context or {}).get("response")
        if resposta is not None and resposta.status_code == 204:
            return b""

        if isinstance(data, dict) and "success" in data:
            return super().render(data, accepted_media_type, renderer_context)  # já envelopado (erros)

        envelope = {
            "success": True,
            "message": getattr(resposta, "message", None) or MENSAGENS_PADRAO.get(getattr(resposta, "status_code", 200), "Operação realizada com sucesso."),
            "data": data,
        }
        return super().render(envelope, accepted_media_type, renderer_context)
