"""Itens GLOBAIS (canais de venda e formas de pagamento): cadastrados uma vez pelo administrador do sistema e liberados a clientes
por SEGMENTO.

Modelo: o item global continua tendo um dono (`cliente` = cliente do sistema, onde o Administrador vive), uma marca `eh_global` e a lista
`segmentos` liberados. Assim a regra de edição não muda: quem não é o dono nunca alcança o item pelo escopo normal
(`clientes_dos_itens`) e recebe 404 ao tentar editar. A visibilidade para uso (listar, escolher na precificação, receber no PDV) é uma
regra SEPARADA e só de leitura, concentrada aqui:

    item aparece para o cliente C  <=>  item.cliente == C   OU   (item.eh_global E o segmento de C está em item.segmentos)

Tirar um segmento de um item global não apaga o que já foi feito com ele (precificações, vendas): só deixa de poder ser escolhido de novo.
"""
from django.db.models import Q

from .acesso import administrador_global, cliente_do_usuario

MENSAGEM_SO_ADMIN = "Somente o administrador do sistema cadastra ou altera itens globais."


def q_globais_do_cliente(modelo, cliente):
    """Q dos itens globais liberados ao segmento do `cliente` (sem os próprios dele). Usa subconsulta (sem JOIN) para não duplicar linhas."""
    if cliente is None or not cliente.segmento_id:
        return Q(pk__in=[])
    return Q(pk__in=modelo.objects.filter(eh_global=True, segmentos=cliente.segmento_id).values("pk"))


def disponiveis_para(modelo, cliente, **filtros):
    """Itens do cliente + globais liberados ao segmento dele (use `ativo=True` em `filtros` para só os ativos)."""
    return modelo.objects.filter(Q(cliente=cliente) | q_globais_do_cliente(modelo, cliente), **filtros).distinct()


def q_globais_do_usuario(modelo, user):
    return q_globais_do_cliente(modelo, cliente_do_usuario(user))


class GlobalFormMixin:
    """Campos `eh_global` e `segmentos` nos formulários de canal/forma de pagamento. Só aparecem (e só valem) para o administrador do
    sistema editando itens do cliente do sistema; para os demais são removidos do formulário, então valor enviado à mão é ignorado.
    Quem não é administrador também não consegue salvar alterações num item que já seja global (mesmo sendo o dono)."""

    def configurar_global(self, solicitante, cliente):
        from apps.modulo.models import Segmento

        self.solicitante = solicitante
        pode = bool(solicitante and administrador_global(solicitante) and cliente is not None and cliente.sistema)
        if pode:
            self.fields["segmentos"].queryset = Segmento.objects.filter(ativo=True)
            self.fields["segmentos"].required = False
            self.fields["segmentos"].widget.attrs["class"] = "form-select"
        else:
            self.fields.pop("eh_global", None)
            self.fields.pop("segmentos", None)
        self._pode_global = pode

    def validar_global(self, dados):
        """Chamar no `clean()`. Global exige ao menos um segmento; não global não guarda segmentos."""
        if self.instance.pk and self.instance.eh_global and not getattr(self, "_pode_global", False):
            raise_erro(MENSAGEM_SO_ADMIN)
        if not getattr(self, "_pode_global", False):
            return dados
        if dados.get("eh_global") and not dados.get("segmentos"):
            self.add_error("segmentos", "Escolha ao menos um segmento para liberar o item global.")
        if not dados.get("eh_global"):
            dados["segmentos"] = []
        return dados


def raise_erro(mensagem):
    from django.core.exceptions import ValidationError

    raise ValidationError(mensagem)
