"""
Imagens de plano e vídeos explicativos do chat (30/09).

A IA marca na resposta onde vai uma mídia com uma tag tipo
[[MIDIA:plano_claro]]. O chat troca a tag pela imagem/vídeo, que é servido
por /midia/<nome> buscando o arquivo no Drive NA HORA (regra do projeto:
imagem de plano nunca vai pro banco).

Como achar o arquivo: dentro da pasta DRIVE_PASTA_MIDIAS_ID, o arquivo cujo
NOME contém a chave (ex: "plano_claro.png"). Pra trocar uma imagem é só subir
outra com o mesmo nome no Drive — sem deploy. Se houver mais de um, vale o
mais recente.

Cache em memória por 1h (o Render reinicia a cada deploy, então isso nunca é
a cópia "oficial" de nada, só evita baixar do Drive a cada lead).
"""
import io
import logging
import threading
import time

from app import google_service
from app.config import DRIVE_PASTA_MIDIAS_ID

logger = logging.getLogger("federal-webchat")

# Lista fechada: só essas chaves podem virar arquivo servido (nada vindo do
# navegador vira busca livre no Drive).
MIDIAS = {
    "plano_federal": "imagem",
    "plano_vivo": "imagem",
    "plano_tim": "imagem",
    "plano_claro": "imagem",
    "video_cadastro": "video",
    "video_ativacao": "video",
}

_CACHE_SEGUNDOS = 3600
_LARGURA_MAX_IMAGEM = 1080  # as artes do Magnific vêm com ~20MB; no celular isso travava o chat

_cache: dict[str, tuple[float, bytes, str]] = {}
_travas: dict[str, threading.Lock] = {nome: threading.Lock() for nome in MIDIAS}


def _reduzir_imagem(conteudo: bytes) -> tuple[bytes, str]:
    try:
        from PIL import Image
    except ImportError:
        return conteudo, "image/png"
    imagem = Image.open(io.BytesIO(conteudo))
    imagem = imagem.convert("RGB")
    if imagem.width > _LARGURA_MAX_IMAGEM:
        altura = round(imagem.height * _LARGURA_MAX_IMAGEM / imagem.width)
        imagem = imagem.resize((_LARGURA_MAX_IMAGEM, altura), Image.LANCZOS)
    saida = io.BytesIO()
    imagem.save(saida, format="JPEG", quality=82, optimize=True, progressive=True)
    return saida.getvalue(), "image/jpeg"


def obter(nome: str) -> tuple[bytes, str] | None:
    """Devolve (bytes, content-type) da mídia, ou None se não existir no Drive."""
    if nome not in MIDIAS:
        return None
    with _travas[nome]:
        em_cache = _cache.get(nome)
        if em_cache and time.time() - em_cache[0] < _CACHE_SEGUNDOS:
            return em_cache[1], em_cache[2]

        arquivos = google_service.buscar_arquivos_por_palavra_chave(DRIVE_PASTA_MIDIAS_ID, nome)
        if not arquivos:
            logger.warning("MIDIA_NAO_ENCONTRADA %s na pasta do Drive %s", nome, DRIVE_PASTA_MIDIAS_ID)
            return None
        arquivo = arquivos[0]
        conteudo = google_service.baixar_arquivo_bytes(arquivo["id"])
        tipo = arquivo.get("mimeType") or "application/octet-stream"
        if MIDIAS[nome] == "imagem":
            conteudo, tipo = _reduzir_imagem(conteudo)

        _cache[nome] = (time.time(), conteudo, tipo)
        logger.info("MIDIA_CARREGADA %s (%s, %d KB)", nome, arquivo.get("name"), len(conteudo) // 1024)
        return conteudo, tipo
