"""
Integração com Google Sheets e Google Drive via service account (28/09,
projeto FLYER-Automacao) — resolve o pedido do Diegão de poder EDITAR e
EXCLUIR linhas de planilhas existentes, e buscar arquivo no Drive por
palavra-chave, em vez de ficar sempre criando arquivo novo.

Pré-requisito pra qualquer função aqui funcionar: a planilha ou pasta do
Drive em questão precisa estar compartilhada como "Editor" com o e-mail da
service account (GOOGLE_SERVICE_ACCOUNT_JSON, campo "client_email").

Se GOOGLE_SERVICE_ACCOUNT_JSON não estiver configurada, todas as funções
levantam RuntimeError com mensagem clara — nunca falham silencioso, pra não
mascarar um problema de configuração.
"""
import json
import logging

from app.config import GOOGLE_SERVICE_ACCOUNT_JSON

logger = logging.getLogger("federal-webchat")

try:
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
except ImportError:  # driver não instalado — só falha se alguém tentar usar
    service_account = None
    build = None
    HttpError = Exception

ESCOPOS = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def disponivel() -> bool:
    return bool(GOOGLE_SERVICE_ACCOUNT_JSON) and service_account is not None


def _credenciais():
    if not disponivel():
        raise RuntimeError(
            "GOOGLE_SERVICE_ACCOUNT_JSON não configurada (ou biblioteca "
            "google-api-python-client não instalada) — funções de "
            "Sheets/Drive via service account indisponíveis."
        )
    info = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)
    return service_account.Credentials.from_service_account_info(info, scopes=ESCOPOS)


def _sheets():
    return build("sheets", "v4", credentials=_credenciais(), cache_discovery=False)


def _drive():
    return build("drive", "v3", credentials=_credenciais(), cache_discovery=False)


# ---------------------------------------------------------------------------
# Google Sheets — ler, adicionar, atualizar e excluir linha
# ---------------------------------------------------------------------------

def ler_aba(planilha_id: str, aba: str) -> list[list[str]]:
    """Lê todas as linhas de uma aba (inclui o cabeçalho na linha 0)."""
    resultado = _sheets().spreadsheets().values().get(
        spreadsheetId=planilha_id, range=aba
    ).execute()
    return resultado.get("values", [])


def adicionar_linha(planilha_id: str, aba: str, valores: list) -> None:
    """Acrescenta uma linha nova no final da aba."""
    _sheets().spreadsheets().values().append(
        spreadsheetId=planilha_id,
        range=aba,
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"values": [valores]},
    ).execute()


def atualizar_linha(planilha_id: str, aba: str, numero_linha: int, valores: list) -> None:
    """
    `numero_linha` é 1-indexado igual aparece no Google Sheets (linha 1 =
    cabeçalho, geralmente). Sobrescreve a linha inteira com `valores`.
    """
    intervalo = f"{aba}!A{numero_linha}"
    _sheets().spreadsheets().values().update(
        spreadsheetId=planilha_id,
        range=intervalo,
        valueInputOption="USER_ENTERED",
        body={"values": [valores]},
    ).execute()


def excluir_linha(planilha_id: str, aba_gid: int, numero_linha: int) -> None:
    """
    Remove a linha de vez (desloca as de baixo pra cima), diferente de só
    limpar o conteúdo. `aba_gid` é o ID numérico da aba (não o nome) — visível
    na URL da planilha depois de "gid=". `numero_linha` é 1-indexado.
    """
    _sheets().spreadsheets().batchUpdate(
        spreadsheetId=planilha_id,
        body={
            "requests": [{
                "deleteDimension": {
                    "range": {
                        "sheetId": aba_gid,
                        "dimension": "ROWS",
                        "startIndex": numero_linha - 1,
                        "endIndex": numero_linha,
                    }
                }
            }]
        },
    ).execute()


# ---------------------------------------------------------------------------
# Google Drive — buscar arquivo por palavra-chave e baixar sob demanda
# (uso pretendido: lookup de imagem de plano pro Federal Connect — a imagem
# NUNCA é salva no banco de dados da aplicação, só buscada no Drive na hora).
# ---------------------------------------------------------------------------

def buscar_arquivos_por_palavra_chave(pasta_id: str, palavra_chave: str) -> list[dict]:
    """
    Procura, dentro de uma pasta específica do Drive, arquivos cujo nome
    contenha `palavra_chave` (case-insensitive). Retorna lista de
    {"id", "name", "mimeType"} — não baixa o conteúdo ainda.
    """
    query = (
        f"'{pasta_id}' in parents and trashed = false and "
        f"name contains '{palavra_chave}'"
    )
    # supportsAllDrives/includeItemsFromAllDrives (30/09): a pasta "FLYER —
    # Tudo" fica num Drive compartilhado; sem isso a busca volta vazia.
    # Mais recente primeiro: se subirem uma versão nova com o mesmo nome,
    # vale a nova.
    resultado = _drive().files().list(
        q=query, fields="files(id, name, mimeType)", pageSize=10,
        orderBy="modifiedTime desc",
        supportsAllDrives=True, includeItemsFromAllDrives=True,
    ).execute()
    return resultado.get("files", [])


def baixar_arquivo_bytes(file_id: str) -> bytes:
    """
    Baixa o conteúdo binário de um arquivo do Drive (ex: imagem de plano)
    pra uso imediato (ex: mandar pro lead no chat). Nunca grava em disco nem
    em banco — quem chamar essa função decide o que fazer com os bytes, e o
    combinado é: só repassar, nunca persistir.
    """
    from googleapiclient.http import MediaIoBaseDownload
    import io

    request = _drive().files().get_media(fileId=file_id, supportsAllDrives=True)
    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, request)
    concluido = False
    while not concluido:
        _, concluido = downloader.next_chunk()
    return buffer.getvalue()
