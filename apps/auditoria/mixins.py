from django.db import transaction

from .utils import registrar_criacao, registrar_edicao, tirar_foto


class AuditoriaFormMixin:
    """Audita automaticamente CREATE e UPDATE de uma CreateView/UpdateView.

    Uso (coloque ANTES das demais classes base):

        class InsumoCreateView(AuditoriaFormMixin, ClienteScopedMixin, CreateView): ...
        class InsumoUpdateView(AuditoriaFormMixin, ClienteScopedMixin, UpdateView): ...

    Como funciona:
    - UPDATE: tira uma "foto" do objeto em get_object(), ANTES de o
      formulário alterá-lo (quando form_valid() roda, o objeto em memória
      já tem os valores novos), e compara com o estado salvo.
    - Salvar + auditar acontecem na MESMA transação: se a auditoria falhar,
      a alteração é desfeita.
    - Models com dinheiro em centavos declaram AUDITORIA_MONETARIOS
      (ver apps/insumo/models.py) para a auditoria mostrar "R$ 40,00".
    """

    modulo_auditoria = None  # por padrão, o app_label do model

    def get_object(self, queryset=None):
        objeto = super().get_object(queryset)
        self._foto_antes = tirar_foto(objeto)
        return objeto

    def form_valid(self, form):
        criando = form.instance.pk is None
        with transaction.atomic():
            resposta = super().form_valid(form)
            if criando:
                registrar_criacao(self.request, self.object, modulo=self.modulo_auditoria)
            else:
                registrar_edicao(self.request, self.object, self._foto_antes, modulo=self.modulo_auditoria)
        return resposta
