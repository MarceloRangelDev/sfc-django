"""Acesso ao Cloudflare R2 (API compatível com S3) — o ÚNICO lugar que fala com o R2.

Os módulos do sistema nunca importam isto: usam `servico.py`. Credenciais vêm de `settings` (variáveis de ambiente) e nunca aparecem em
mensagens de erro nem em logs. Nos testes, `ArmazenamentoMemoria` substitui o R2 (nenhuma chamada externa é feita).
"""
import logging

from django.conf import settings

logger = logging.getLogger(__name__)


class ArmazenamentoIndisponivel(Exception):
    """R2 não configurado ou inacessível. A mensagem é segura para exibir (sem segredos)."""


def configurado():
    return all([settings.R2_ENDPOINT, settings.R2_ACCESS_KEY_ID, settings.R2_SECRET_ACCESS_KEY, settings.R2_BUCKET])


class ArmazenamentoR2:
    def __init__(self):
        if not configurado():
            raise ArmazenamentoIndisponivel("Armazenamento de imagens não configurado. Avise o administrador da plataforma.")
        import boto3
        from botocore.config import Config

        self.bucket = settings.R2_BUCKET
        # Região "auto" é a exigida pelo R2; timeouts curtos e poucas tentativas para não travar a requisição.
        self.s3 = boto3.client(
            "s3", endpoint_url=settings.R2_ENDPOINT, aws_access_key_id=settings.R2_ACCESS_KEY_ID, aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
            region_name="auto", config=Config(signature_version="s3v4", connect_timeout=5, read_timeout=15, retries={"max_attempts": 2}),
        )

    def _executar(self, operacao, **kwargs):
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            return getattr(self.s3, operacao)(Bucket=self.bucket, **kwargs)
        except (BotoCoreError, ClientError) as erro:
            # Registra só o código/tipo do erro (nunca a requisição, que carregaria cabeçalhos assinados).
            logger.warning("R2 %s falhou: %s", operacao, type(erro).__name__)
            raise ArmazenamentoIndisponivel("O armazenamento de imagens está indisponível no momento. Tente novamente.") from erro

    def enviar(self, chave, dados, mime):
        self._executar("put_object", Key=chave, Body=dados, ContentType=mime)

    def ler(self, chave):
        return self._executar("get_object", Key=chave)["Body"].read()

    def existe(self, chave):
        from botocore.exceptions import ClientError

        try:
            self._executar("head_object", Key=chave)
            return True
        except ArmazenamentoIndisponivel as erro:
            if isinstance(erro.__cause__, ClientError) and erro.__cause__.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return False
            raise

    def excluir(self, chave):
        self._executar("delete_object", Key=chave)

    def url_leitura(self, chave, segundos):
        """URL pré-assinada de LEITURA de um único objeto, de curta duração. É credencial temporária: não persistir nem logar."""
        return self.s3.generate_presigned_url("get_object", Params={"Bucket": self.bucket, "Key": chave}, ExpiresIn=segundos)

    def verificar(self):
        """Teste administrativo: confirma bucket e credenciais (HeadBucket)."""
        self._executar("head_bucket")


class ArmazenamentoMemoria:
    """Substituto em memória para testes: mesma interface, sem rede."""

    def __init__(self):
        self.objetos = {}

    def enviar(self, chave, dados, mime):
        self.objetos[chave] = (bytes(dados), mime)

    def ler(self, chave):
        try:
            return self.objetos[chave][0]
        except KeyError as erro:
            raise ArmazenamentoIndisponivel("Objeto não encontrado.") from erro

    def existe(self, chave):
        return chave in self.objetos

    def excluir(self, chave):
        self.objetos.pop(chave, None)

    def url_leitura(self, chave, segundos):
        return f"https://r2.invalid/{chave}?expira={segundos}"

    def verificar(self):
        return None


_instancia = None


def obter():
    """Cliente único do processo (criado sob demanda para não exigir R2 configurado em telas que não usam imagens)."""
    global _instancia
    if _instancia is None:
        _instancia = ArmazenamentoR2()
    return _instancia
