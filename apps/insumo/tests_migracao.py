"""Testa a migração de dados do Insumo (0002 -> 0005) com registros reais:
unidade_medida (texto) -> medida (FK) e preco (Decimal) -> preco_pago (centavos).

Roda com MigrationExecutor: volta o banco de teste ao estado antigo, cria
insumos no formato antigo, migra para frente, confere, migra para trás e
confere de novo. No fim, restaura o banco para a última migration.
"""
from decimal import Decimal

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

ANTES = [("insumo", "0002_alter_insumo_options_insumo_ativo_insumo_categoria_and_more"), ("medida", "0002_carga_inicial_medidas")]
DEPOIS = [("insumo", "0005_remover_colunas_antigas_insumo")]


def migrar(alvos):
    executor = MigrationExecutor(connection)
    executor.migrate(alvos)
    return MigrationExecutor(connection).loader.project_state(alvos).apps


class MigracaoInsumoTests(TransactionTestCase):
    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())

    def _criar_formato_antigo(self, apps):
        Cliente = apps.get_model("cliente", "Cliente")
        Categoria = apps.get_model("categoria", "Categoria")
        Insumo = apps.get_model("insumo", "Insumo")
        cliente = Cliente.objects.create(tipo_pessoa="PJ", cpf_cnpj="11222333000181", nome_razao_social="Empresa")
        categoria = Categoria.objects.create(cliente=cliente, nome="Laticínios")
        dados = [
            ("Queijo", "G", Decimal("100"), Decimal("4.0000")),
            ("Leite", "ML", Decimal("500"), Decimal("10.0000")),
            ("Farinha", "KG", Decimal("1"), Decimal("1250.7500")),
            ("Ovo", "UN", Decimal("12"), Decimal("0.0500")),
            ("Fracao", "KG", Decimal("2"), Decimal("1.0050")),  # 100,5 centavos -> arredonda
            ("Sem quantidade", "L", Decimal("0"), Decimal("9.9900")),
        ]
        for nome, unidade, quantidade, preco in dados:
            Insumo.objects.create(
                cliente=cliente, categoria=categoria, nome=nome, unidade_medida=unidade, quantidade=quantidade, preco=preco
            )

    def test_migracao_para_frente_e_para_tras(self):
        apps_antigo = migrar(ANTES)
        self._criar_formato_antigo(apps_antigo)

        apps_novo = migrar(DEPOIS)
        Insumo = apps_novo.get_model("insumo", "Insumo")
        por_nome = {i.nome: i for i in Insumo.objects.select_related("medida")}

        esperado = {  # nome: (sigla, preco_pago, preco_unidade)
            "Queijo": ("g", 400, 4000),            # 100 g por R$ 4,00 = R$ 40,00/kg
            "Leite": ("ml", 1000, 2000),           # 500 ml por R$ 10,00 = R$ 20,00/L
            "Farinha": ("kg", 125075, 125075),
            "Ovo": ("un", 5, 0),                   # R$ 0,05 / 12 un = 0,4 centavo -> 0
            "Fracao": ("kg", 101, 51),             # 100,5 -> 101 (ROUND_HALF_UP); 101/2 = 50,5 -> 51
            "Sem quantidade": ("L", 999, None),    # quantidade 0: não dá para calcular
        }
        for nome, (sigla, pago, por_unidade) in esperado.items():
            insumo = por_nome[nome]
            self.assertEqual((insumo.medida.sigla, insumo.preco_pago, insumo.preco_unidade), (sigla, pago, por_unidade), nome)
            self.assertIsInstance(insumo.preco_pago, int)
            self.assertIsNotNone(insumo.categoria_id)  # coluna oculta preservada, dados intactos

        # Reverso: volta ao formato antigo sem perder o essencial.
        apps_reverso = migrar(ANTES)
        InsumoAntigo = apps_reverso.get_model("insumo", "Insumo")
        antigos = {i.nome: i for i in InsumoAntigo.objects.all()}
        self.assertEqual(antigos["Queijo"].unidade_medida, "G")
        self.assertEqual(antigos["Queijo"].preco, Decimal("4"))
        self.assertEqual(antigos["Leite"].unidade_medida, "ML")
        self.assertEqual(antigos["Farinha"].preco, Decimal("1250.75"))
