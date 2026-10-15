"""
Cria/atualiza o catálogo de módulos e os segmentos padrão.

O catálogo abaixo é a fonte de verdade de menu, permissões e matriz de
papéis (ver apps/modulo/README.md). Para um módulo novo, basta cadastrá-lo na
tela "Módulos" ou acrescentá-lo aqui e rodar:

    python manage.py seed_modulos

É idempotente. Módulos de negócio novos são acrescentados aos segmentos
padrão e habilitados para clientes que ainda não têm vínculo (nunca
sobrescreve o que um administrador desabilitou).
"""
from django.core.management.base import BaseCommand

from apps.acl.padroes import aplicar_padrao_aos_modulos
from apps.cliente.models import Cliente
from apps.modulo.models import ClienteModulo, Modulo, Segmento

P, C = Modulo.SECAO_PRINCIPAL, Modulo.SECAO_CONFIGURACOES

# slug: dict(campos do Modulo). `essencial` = só depende da permissão (Configurações).
MODULOS_PADRAO = {
    "insumo": dict(nome="Insumos", descricao="Cadastro de insumos utilizados nas receitas/produtos.", icone="ti tabler-basket", rota="insumo", app="insumo", permissao="insumo.view_insumo", secao=P, ordem=10),
    "processado": dict(nome="Processados", descricao="Processados e receitas.", icone="ti tabler-chef-hat", rota="processado", app="processado", permissao="processado.view_processado", secao=P, ordem=20),
    "categoria": dict(nome="Categorias", descricao="Categorias usadas para organizar insumos e produtos.", icone="ti tabler-tags", rota="categoria", app="categoria", permissao="categoria.view_categoria", secao=P, ordem=30),
    "compras": dict(nome="Lista de compras", descricao="Lista de compras com sugestões do estoque, itens avulsos e serviços.", icone="ti tabler-shopping-cart-plus", rota="compras", app="compras", permissao="compras.view_itemcompra", secao=P, ordem=17),
    "cotacao": dict(nome="Cotações", descricao="Cotações de preços entre lojas, comparativo, vencedores e contas a pagar.", icone="ti tabler-report-search", rota="cotacao", app="cotacao", permissao="cotacao.view_cotacao", secao=P, ordem=18),
    "pdv": dict(nome="PDV", descricao="Caixa, vendas, comandas de mesa e comprovantes.", icone="ti tabler-cash-register", rota="pdv", app="pdv", permissao="pdv.view_venda", secao=P, ordem=12),
    "consumidor": dict(nome="Consumidores", descricao="Clientes finais da empresa (cadastro, contatos e consentimento LGPD).", icone="ti tabler-users-group", rota="consumidor", app="consumidor", permissao="consumidor.view_consumidor", secao=P, ordem=45),
    "produto": dict(nome="Produto", descricao="Cadastro de produtos.", icone="ti tabler-package", rota="produto", app="produto", permissao="produto.view_produto", secao=P, ordem=40),
    "ficha": dict(nome="Ficha Técnica", descricao="Fichas técnicas de produtos/receitas.", icone="ti tabler-clipboard-list", rota="ficha", app="ficha", permissao="ficha.view_ficha", secao=P, ordem=50),
    "estoque": dict(nome="Estoque", descricao="Saldos, lotes, validade, movimentações e alertas.", icone="ti tabler-packages", rota="estoque", app="estoque", permissao="estoque.view_movimentoestoque", secao=P, ordem=15),
    # Linha da matriz de papéis: quem pode ver/registrar transferências entre matriz e filiais (sem item de menu).
    "estoque-transferencia": dict(nome="Transferências de estoque", descricao="Transferir estoque entre matriz e filiais.", icone="ti tabler-transfer", rota="", app="estoque", permissao="estoque.view_transferenciaestoque", secao=P, ordem=16, essencial=True),
    "financeiro-pagar": dict(nome="Contas a pagar", descricao="Contas a pagar, baixas e vencimentos.", icone="ti tabler-arrow-up-right", rota="conta-pagar-list", app="financeiro", permissao="financeiro.view_contapagar", secao=P, ordem=70),
    "financeiro-receber": dict(nome="Contas a receber", descricao="Contas a receber, baixas e vencimentos.", icone="ti tabler-arrow-down-left", rota="conta-receber-list", app="financeiro", permissao="financeiro.view_contareceber", secao=P, ordem=71),
    "financeiro-relatorios": dict(nome="DRE e Balanço", descricao="Relatórios financeiros gerenciais.", icone="ti tabler-report-money", rota="financeiro-dre", app="financeiro", permissao="financeiro.view_relatoriofinanceiro", secao=P, ordem=72),
    # Só linha da matriz de papéis (sem menu): quem gerencia as categorias financeiras.
    "financeiro-categorias": dict(nome="Categorias financeiras", descricao="Categorias de receitas e despesas (agrupam o DRE).", icone="ti tabler-tags", rota="", app="financeiro", permissao="financeiro.view_categoriafinanceira", secao=P, ordem=73, essencial=True),
    "custo-operacional": dict(nome="Custo operacional", descricao="Custos fixos e variáveis distribuídos por categoria.", icone="ti tabler-receipt", rota="custo-list", app="custo", permissao="custo.view_custooperacional", secao=P, ordem=55),
    "precificacao": dict(nome="Precificação", descricao="Cálculo de preços de produtos e serviços.", icone="ti tabler-calculator", rota="precificacao", app="precificacao", permissao="precificacao.view_precificacao", secao=P, ordem=60),
    # --- Configurações (essenciais: só dependem da permissão do perfil) ---
    "cliente": dict(nome="Clientes", descricao="Clientes, matriz e filiais.", icone="ti tabler-building-store", rota="cliente-list", app="cliente", permissao="cliente.view_cliente", secao=C, ordem=10, essencial=True),
    "itens-filiais": dict(nome="Itens das filiais", descricao="Ver, criar, editar e excluir itens das filiais (para usuários da matriz).", icone="ti tabler-building-store", rota="", app="cliente", permissao="cliente.view_itensfilial", secao=C, ordem=15, essencial=True),
    "canal-venda": dict(nome="Canal de vendas", descricao="Canais de venda e suas taxas.", icone="ti tabler-shopping-cart", rota="canal-list", app="canal", permissao="canal.view_canalvenda", secao=C, ordem=35, essencial=True),
    "custo-comum": dict(nome="Custos comuns", descricao="Custos comuns usados no custo operacional (água, energia, aluguel...).", icone="ti tabler-list-details", rota="custo-comum-list", app="custo", permissao="custo.view_customodelo", secao=C, ordem=36, essencial=True),
    "forma-pagamento": dict(nome="Formas de pagamento", descricao="Formas de pagamento do PDV e suas taxas (percentual, valor fixo por transação ou ambos).", icone="ti tabler-credit-card", rota="forma-pagamento-list", app="pdvconfig", permissao="pdvconfig.view_formapagamento", secao=C, ordem=37, essencial=True),
    "mesa": dict(nome="Mesas", descricao="Mesas e comandas do PDV.", icone="ti tabler-armchair", rota="mesa-list", app="pdvconfig", permissao="pdvconfig.view_mesa", secao=C, ordem=38, essencial=True),
    "pdv-config": dict(nome="Configurações do PDV", descricao="Baixa de estoque, comprovante e rodapé do PDV.", icone="ti tabler-settings", rota="pdv-config", app="pdvconfig", permissao="pdvconfig.view_configuracaopdv", secao=C, ordem=39, essencial=True),
    "usuario": dict(nome="Usuários", descricao="Usuários vinculados a cliente e perfil.", icone="ti tabler-users", rota="usuario-list", app="usuario", permissao="auth.view_user", secao=C, ordem=20, essencial=True),
    "notificacao": dict(nome="Notificações", descricao="Envio e histórico de notificações.", icone="ti tabler-bell", rota="notificacao-list", app="notificacao", permissao="notificacao.view_notificacao", secao=C, ordem=30, essencial=True),
    "modulo": dict(nome="Módulos", descricao="Catálogo de módulos do sistema.", icone="ti tabler-apps", rota="modulo-catalogo", app="modulo", permissao="modulo.view_modulo", secao=C, ordem=40, essencial=True),
    "segmento": dict(nome="Segmentos", descricao="Segmentos de trabalho e seus módulos.", icone="ti tabler-category", rota="segmento-list", app="modulo", permissao="modulo.view_segmento", secao=C, ordem=50, essencial=True),
    "modulo-cliente": dict(nome="Módulos do cliente", descricao="Habilitar módulos por cliente.", icone="ti tabler-toggle-right", rota="modulo-list", app="modulo", permissao="modulo.change_clientemodulo", secao=C, ordem=60, essencial=True),
    "medida": dict(nome="Medidas", descricao="Unidades de medida.", icone="ti tabler-ruler-measure", rota="medida-list", app="medida", permissao="medida.view_medida", secao=C, ordem=70, essencial=True),
    "auditoria": dict(nome="Auditoria", descricao="Log de auditoria.", icone="ti tabler-history", rota="auditoria-list", app="auditoria", permissao="auditoria.view_registroauditoria", secao=C, ordem=80, essencial=True, delegavel=False),
    "acl-papel": dict(nome="Papéis", descricao="Perfis de acesso e suas permissões.", icone="ti tabler-shield-lock", rota="acl-papel-list", app="acl", permissao="auth.view_group", secao=C, ordem=90, essencial=True, delegavel=False),
    "acl-permissao": dict(nome="Permissões", descricao="Catálogo de permissões.", icone="ti tabler-key", rota="acl-permissao-list", app="acl", permissao="auth.view_permission", secao=C, ordem=100, essencial=True, delegavel=False),
}

SEGMENTOS_PADRAO = {
    "servico": ("Serviço", Segmento.TIPO_SERVICO),
    "produto": ("Produto", Segmento.TIPO_PRODUTO),
    "ambos": ("Serviço e Produto", Segmento.TIPO_AMBOS),
}

def instrucoes_padrao(slug, campos):
    """Texto inicial de "Instruções de uso" de um módulo do catálogo."""
    secao = dict(Modulo.SECAO_CHOICES)[campos["secao"]]
    acesso = (
        "Acesso: só depende da permissão do perfil (módulo essencial)."
        if campos.get("essencial")
        else "Acesso: o módulo precisa estar no segmento do cliente, habilitado para o cliente e permitido ao perfil."
    )
    return (
        f"Onde aparece: {secao} > {campos['nome']} (ordem {campos['ordem']}).\n"
        f"Rota: nome da URL '{campos['rota']}' no urls.py do app '{campos['app']}', protegida por "
        f"module_required('{slug}', '{campos['permissao']}').\n"
        f"{acesso}"
    )


class Command(BaseCommand):
    help = "Cria/atualiza o catálogo de módulos, os segmentos padrão e as habilitações iniciais."

    def handle(self, *args, **options):
        modulos = {}
        for slug, campos in MODULOS_PADRAO.items():
            campos = {"essencial": False, "delegavel": True, **campos}
            # `instrucoes` só é preenchida se o administrador ainda não escreveu as suas.
            modulo, criado = Modulo.objects.update_or_create(slug=slug, defaults={**campos, "ativo": True})
            if not modulo.instrucoes:
                modulo.instrucoes = instrucoes_padrao(slug, campos)
                modulo.save(update_fields=["instrucoes"])
            modulos[slug] = modulo
            self.stdout.write(self.style.SUCCESS(f"Módulo '{modulo.nome}' ({slug}) {'criado' if criado else 'atualizado'}."))

        negocio = [m for m in modulos.values() if not m.essencial]
        for slug, (nome, tipo) in SEGMENTOS_PADRAO.items():
            segmento, _ = Segmento.objects.get_or_create(slug=slug, defaults={"nome": nome, "tipo_precificacao": tipo})
            segmento.modulos.add(*negocio)

        # Clientes sem nenhum vínculo com o módulo passam a tê-lo habilitado
        # (clientes novos nascem sem módulos; ver README do app).
        novos = 0
        for cliente in Cliente.objects.filter(segmento__isnull=False):
            for modulo in negocio:
                _obj, criado = ClienteModulo.objects.get_or_create(cliente=cliente, modulo=modulo, defaults={"ativo": True})
                novos += criado
        # Módulos habilitados precisam estar EFETIVOS: completa as permissões padrão dos perfis (só onde faltam).
        aplicar_padrao_aos_modulos(negocio)
        self.stdout.write(self.style.SUCCESS(f"{novos} vínculo(s) cliente-módulo criado(s). Módulos aplicados com sucesso."))
