from django.conf import settings
from django.db import models

from web_project.regras import extenso
from web_project.regras.conversao import DIMENSOES


class Medida(models.Model):
    """Unidade de medida (Gramas, Quilo, Litro, Colher de sopa...).

    É um cadastro GLOBAL do sistema (não pertence a um Cliente): todos os
    clientes compartilham as mesmas unidades, e só administradores as
    editam (ver apps/medida/urls.py). Todas as conversões e o cálculo de
    preço por unidade usam esta tabela — nada de fatores hardcoded no
    código (ver web_project/regras/conversao.py).

    Campos além dos pedidos no PROMPT_MESTRE (seção 23), necessários porque
    o campo `equivale` é só um texto de exibição ("1000 g = 1 Kg") e não dá
    para calcular a partir dele:
      - dimensao:   grupo em que a unidade pode ser convertida (massa,
                    volume, comprimento...). Só se converte dentro do grupo.
      - fator_base: quanto vale 1 desta unidade na unidade base da dimensão
                    (massa: grama; volume: mL; comprimento: m...). Ex.: o
                    quilo tem fator 1000 (1 kg = 1000 g).
      - nome_singular / nome_plural: para escrever "1 grama" / "100 gramas".
    """

    medida = models.CharField(max_length=60, unique=True, verbose_name="Medida")
    sigla = models.CharField(max_length=15, unique=True, verbose_name="Sigla")
    descricao = models.CharField(max_length=255, blank=True, verbose_name="Descrição")
    equivale = models.CharField(
        max_length=100, blank=True, verbose_name="Equivale a", help_text="Texto de exibição, ex.: 1000 g = 1 Kg."
    )
    dimensao = models.CharField(max_length=15, choices=list(DIMENSOES.items()), verbose_name="Dimensão")
    fator_base = models.DecimalField(
        max_digits=20,
        decimal_places=6,
        verbose_name="Fator de conversão",
        help_text="Quanto vale 1 desta unidade na unidade base da dimensão (ex.: 1 quilo = 1000 gramas).",
    )
    nome_singular = models.CharField(max_length=60, blank=True, verbose_name="Nome no singular")
    nome_plural = models.CharField(max_length=60, blank=True, verbose_name="Nome no plural")
    ativo = models.BooleanField(default=True, verbose_name="Ativo")
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        db_column="id_usuario",
        related_name="medidas_cadastradas",
        verbose_name="Cadastrado por",
    )
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "medidas"
        verbose_name = "Medida"
        verbose_name_plural = "Medidas"
        ordering = ["id"]

    def __str__(self):
        return self.medida

    @property
    def singular(self):
        return self.nome_singular or self.medida.lower()

    @property
    def plural(self):
        return self.nome_plural or extenso.plural_padrao(self.singular)

    def por_extenso(self, quantidade):
        """'100' -> '100 gramas' (função global de web_project/regras/extenso.py)."""
        return extenso.quantidade_por_extenso(quantidade, self.singular, self.plural)

    def em_uso(self):
        """True se algum registro de qualquer tabela referencia esta medida.

        Percorre as relações reversas do próprio Django, então novos models
        que apontarem para Medida passam a contar automaticamente, sem
        precisar lembrar de atualizar esta regra.
        """
        for relacao in self._meta.related_objects:
            if relacao.related_model._default_manager.filter(**{relacao.field.name: self}).exists():
                return True
        return False
