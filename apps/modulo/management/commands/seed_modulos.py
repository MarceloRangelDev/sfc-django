"""
Cria/atualiza um Modulo para cada tela de negócio do sistema e garante que
todo Cliente já cadastrado continue com esses módulos habilitados (para
não quebrar o acesso de quem já usava o sistema antes da Fase 3).

Clientes cadastrados A PARTIR de agora começam sem nenhum módulo habilitado
— cabe a um Administrador habilitar, na tela "Módulos do Cliente", quais
telas fazem sentido para o segmento daquele cliente (ex: um cliente do
segmento de alimentação ganha o módulo de Receitas/Fichas Técnicas).

Rodar sempre que uma nova tela/módulo for adicionado ao sistema:

    python manage.py seed_modulos

É idempotente: pode ser rodado várias vezes sem duplicar nada.
"""
from django.core.management.base import BaseCommand

from apps.cliente.models import Cliente
from apps.modulo.models import ClienteModulo, Modulo

# slug -> (nome, descrição, ícone)
MODULOS_PADRAO = {
    "insumo": ("Insumos", "Cadastro de insumos utilizados nas receitas/produtos.", "ti tabler-app-window"),
    "categoria": ("Categorias", "Categorias usadas para organizar insumos e produtos.", "ti tabler-app-window"),
    "produto": ("Produto", "Cadastro de produtos.", "ti tabler-app-window"),
    "processado": ("Processados", "Processados e receitas.", "ti tabler-app-window"),
    "ficha": ("Ficha Técnica", "Fichas técnicas de produtos/receitas.", "ti tabler-app-window"),
    "precificacao": ("Precificação", "Cálculo de preços de produtos e serviços.", "ti tabler-app-window"),
}


class Command(BaseCommand):
    help = "Cria/atualiza os módulos padrão e mantém habilitados para clientes já existentes."

    def handle(self, *args, **options):
        modulos = {}
        for slug, (nome, descricao, icone) in MODULOS_PADRAO.items():
            modulo, created = Modulo.objects.update_or_create(
                slug=slug,
                defaults={"nome": nome, "descricao": descricao, "icone": icone, "ativo": True},
            )
            modulos[slug] = modulo
            status = "criado" if created else "atualizado"
            self.stdout.write(self.style.SUCCESS(f"Módulo '{nome}' ({slug}) {status}."))

        # Clientes que já existiam antes da Fase 3 continuam com todos os
        # módulos habilitados — só clientes criados depois de agora que
        # nascem sem nenhum módulo (habilitação passa a ser deliberada).
        clientes = Cliente.objects.all()
        total_vinculos = 0
        for cliente in clientes:
            for modulo in modulos.values():
                _obj, created = ClienteModulo.objects.get_or_create(
                    cliente=cliente, modulo=modulo, defaults={"ativo": True}
                )
                if created:
                    total_vinculos += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"{total_vinculos} vínculo(s) cliente-módulo criado(s) para {clientes.count()} cliente(s) existente(s)."
            )
        )
        self.stdout.write(self.style.SUCCESS("Módulos padrão aplicados com sucesso."))
