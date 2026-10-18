"""Registro das entidades que podem ter imagem e das regras de AUTORIZAÇÃO de cada uma.

Para um módulo novo ganhar imagem: cadastre-o em ENTIDADES (modelo, categoria, regras de ver/alterar). Nenhuma regra de acesso fica
espalhada nas views: o serviço e a entrega sempre perguntam aqui. O tenant (Cliente) vem da própria entidade, nunca do cliente HTTP.
"""
from dataclasses import dataclass
from typing import Callable

from django.apps import apps
from web_project.decorators import modulo_habilitado_para
from web_project.acesso import clientes_acessiveis, clientes_dos_itens, usuarios_acessiveis

CATALOGO, PRIVADA = "catalogo", "privada"  # CATALOGO pode usar a entrega via Cloudflare (cache); PRIVADA é sempre servida pelo Django


@dataclass(frozen=True)
class Entidade:
    modelo: str                                    # "app.Modelo"
    categoria: str
    rotulo: str
    obter_para_ver: Callable                       # (user, pk) -> objeto ou None
    obter_para_alterar: Callable
    cliente_de: Callable                           # objeto -> Cliente (tenant dono da imagem)


def _catalogo(modelo, modulo, perm_ver, extras_ver, perm_alterar):
    """Insumos/produtos: escopo por unidade (matriz alcança filiais só com a permissão de itens de filial). `extras_ver` = pares
    (módulo, permissão) de quem só USA o item (cotação/PDV) e por isso apenas vê a foto. O módulo precisa estar habilitado."""
    classe = lambda: apps.get_model(modelo)
    pode = lambda user, mod, perm: user.has_perm(perm) and modulo_habilitado_para(user, mod)

    def ver(user, pk):
        if not any(pode(user, m, p) for m, p in ((modulo, perm_ver), *extras_ver)):
            return None
        return classe().objects.filter(pk=pk, cliente__in=clientes_dos_itens(user, "view")).first()

    def alterar(user, pk):
        if not pode(user, modulo, perm_alterar):
            return None
        return classe().objects.filter(pk=pk, cliente__in=clientes_dos_itens(user, "change")).first()

    return ver, alterar


def _usuario_ver(user, pk):
    if user.pk == pk:
        return user
    if not user.has_perm("auth.view_user"):
        return None
    return usuarios_acessiveis(user).filter(pk=pk).first()


def _usuario_alterar(user, pk):
    if user.pk == pk:
        return user if user.has_perm("accounts.change_fotoperfil") else None  # cada um troca a própria foto, se o papel permitir
    if not user.has_perm("auth.change_user"):
        return None
    return usuarios_acessiveis(user).filter(pk=pk).first()


def _cliente_ver(user, pk):
    if not user.has_perm("cliente.view_cliente"):
        return None
    return clientes_acessiveis(user).filter(pk=pk).first()


def _cliente_alterar(user, pk):
    if not user.has_perm("cliente.change_cliente"):
        return None
    return clientes_acessiveis(user).filter(pk=pk).first()


def _tenant_do_usuario(usuario):
    return getattr(getattr(usuario, "profile", None), "cliente", None)


_ins_ver, _ins_alt = _catalogo("insumo.Insumo", "insumo", "insumo.view_insumo", (("cotacao", "cotacao.view_cotacao"),), "insumo.change_insumo")
_prod_ver, _prod_alt = _catalogo("produto.Produto", "produto", "produto.view_produto", (("pdv", "pdv.add_venda"), ("pdv", "pdv.view_venda")), "produto.change_produto")

def _globais(modelo):
    """Entidades do cadastro global (insumo padrão e variação por marca): sem tenant; veem quem usa insumos ou administra o cadastro global."""
    classe = lambda: apps.get_model(modelo)

    def ver(user, pk):
        if not (user.has_perm("insumo.view_insumo") or user.has_perm("insumoglobal.view_insumoglobal")):
            return None
        return classe().objects.filter(pk=pk).first()

    def alterar(user, pk):
        return classe().objects.filter(pk=pk).first() if user.has_perm("insumoglobal.change_insumoglobal") else None

    return ver, alterar


_marca_ver, _marca_alterar = _globais("insumoglobal.InsumoGlobalMarca")


ENTIDADES = {
    "insumo": Entidade("insumo.Insumo", CATALOGO, "Insumo", _ins_ver, _ins_alt, lambda o: o.cliente),
    "produto": Entidade("produto.Produto", CATALOGO, "Produto", _prod_ver, _prod_alt, lambda o: o.cliente),
    # Imagens do cadastro global: SÓ a da marca (variação insumo+marca); o insumo padrão em si não tem foto. Sem tenant: fica na unidade de quem envia.
    "insumo_global_marca": Entidade("insumoglobal.InsumoGlobalMarca", CATALOGO, "Marca do insumo global", _marca_ver, _marca_alterar, lambda o: None),
    "usuario": Entidade("auth.User", PRIVADA, "Usuário", _usuario_ver, _usuario_alterar, _tenant_do_usuario),
    "cliente": Entidade("cliente.Cliente", PRIVADA, "Cliente", _cliente_ver, _cliente_alterar, lambda o: o),
}


def entidade(tipo):
    return ENTIDADES.get(tipo)


def pode_ver(user, tipo, pk):
    reg = ENTIDADES.get(tipo)
    return bool(reg and user.is_authenticated and reg.obter_para_ver(user, pk) is not None)


def tipo_do_modelo(modelo):
    """Tipo de entidade de um modelo Django (usado pelos sinais de exclusão)."""
    rotulo = modelo._meta.label
    for tipo, reg in ENTIDADES.items():
        if reg.modelo == rotulo:
            return tipo
    return None
