"""Serviços de negócio para documentos da DF: log de acesso (LGPD Art. 37),
heurística de detecção de PII, resolução de escopo, cálculo de retenção, série
de crescimento de armazenamento e a costura do backend de storage
(resposta_download — o único ponto que serve o conteúdo do arquivo)."""
import re
from collections import defaultdict
from datetime import date, timezone

from dateutil.relativedelta import relativedelta
from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncMonth
from django.http import FileResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404

from df.models import DocumentoDF, LogAcessoDocumento
from usuarios.models import Empresa

# Padrões de texto de item de checklist que costumam indicar dado pessoal de
# terceiros (cotistas). Heurística para pré-marcar o checkbox no upload — o
# usuário sempre pode corrigir manualmente; isto nunca decide sozinho.
_PADRAO_PII = re.compile(
    r"cotista|cadastr|identifica|resid|banc[aá]ri|kyc|pld",
    re.IGNORECASE,
)


def sugerir_contem_dados_pessoais(texto: str) -> bool:
    """Indica se o texto do item de checklist sugere conteúdo com dados
    pessoais de terceiros. Usado para pré-marcar `contem_dados_pessoais` no
    upload — nunca para decidir sozinho."""
    if not texto:
        return False
    return bool(_PADRAO_PII.search(texto))


def _client_ip(request):
    """Mesmo padrão de usuarios/views_password_reset.py — considera proxy reverso."""
    x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded_for:
        return x_forwarded_for.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def registrar_acesso(documento, request, acao):
    """Grava uma linha em LogAcessoDocumento.

    Append-only: nenhuma outra rota do sistema deve dar update/delete numa
    linha desse model. `acao` é um valor de LogAcessoDocumento.Acao (ex.:
    "upload", "download", "exclusao", "negado" — este último para tentativas
    de acesso fora de escopo, registradas antes que virem incidente).
    """
    usuario = request.user if request.user.is_authenticated else None
    LogAcessoDocumento.objects.create(
        documento=documento,
        documento_id_hist=documento.id,
        empresa=documento.empresa,
        usuario=usuario,
        acao=acao,
        ip_address=_client_ip(request),
        user_agent=request.META.get("HTTP_USER_AGENT", "")[:500],
    )


def resolver_documento_escopo(documento_id, empresa, request):
    """Busca o documento SEM filtrar por empresa (de propósito) e confere o
    tenant explicitamente, registrando NEGADO no log quando não bate.

    Um `get_object_or_404(DocumentoDF, id=..., empresa=empresa)` faria a
    mesma checagem, mas devolveria 404 em silêncio — escondendo a tentativa
    de acesso indevido em vez de deixar rastro dela (LGPD Art. 37). Por isso
    aqui o documento é resolvido primeiro, para poder ser logado mesmo
    quando o acesso será negado.

    Retorna o documento se pertence à empresa informada, ou None (já com o
    log de acesso negado registrado) caso contrário.
    """
    documento = get_object_or_404(DocumentoDF, id=documento_id)
    if documento.empresa_id != empresa.id:
        registrar_acesso(documento, request, LogAcessoDocumento.Acao.NEGADO)
        return None
    return documento


def calcular_retencao_ate(periodo, empresa):
    """Data a partir da qual o documento pode ser expurgado: 31/dez do ano do
    período + empresa.retencao_anos anos.

    Configurável por empresa (LGPD Art. 6º, III — necessidade): o prazo de
    guarda varia por contrato, o padrão de 5 anos é só o piso mais citado
    (Lei 9.613/98 art. 10; guarda contábil). Nunca dispara expurgo sozinho —
    isso é o campo, não a ação; a Fase 5.5 é quem decide o que fazer com ele,
    e sempre com confirmação manual.
    """
    return date(periodo.ano + empresa.retencao_anos, 12, 31)


def uso_documentos_bytes(empresa) -> int:
    """Soma de tamanho_bytes dos documentos ativos (não excluídos) da empresa.

    Não denormalizado: para o volume esperado (dezenas de milhares de linhas
    por empresa ao longo de anos), a soma é instantânea e evita o risco de um
    contador ficar dessincronizado do estado real.
    """
    total = (
        DocumentoDF.objects
        .filter(empresa=empresa, excluido_em__isnull=True)
        .aggregate(total=Sum("tamanho_bytes"))["total"]
    )
    return total or 0


def estatisticas_por_empresa():
    """Uma linha por empresa (`Empresa` anotada): uso em bytes, contagem de
    documentos ativos, contagem com PII. Uma única query agregada — não itera
    empresa por empresa (mesma disciplina anti-N+1 de api_checklist_periodo).

    Usado pelo painel administrativo (Fase 5) e por nada mais — não chama
    uso_documentos_bytes() (que soma uma empresa por vez) porque aqui o
    objetivo é justamente evitar isso para todas as empresas de uma vez.
    """
    ativo = Q(documentos_df__excluido_em__isnull=True)
    return Empresa.objects.annotate(
        documentos_ativos=Count("documentos_df", filter=ativo),
        uso_bytes=Sum("documentos_df__tamanho_bytes", filter=ativo),
        documentos_pii=Count("documentos_df", filter=ativo & Q(documentos_df__contem_dados_pessoais=True)),
    ).order_by("nome")


def serie_crescimento_mensal():
    """Uso acumulado de armazenamento (GB) por mês — total da plataforma e
    por empresa. Conta TODOS os documentos, não só os ativos: um documento
    excluído logicamente continua fisicamente no disco até o expurgo (Fase
    5.5) — a exclusão lógica nunca toca no arquivo, só o expurgo apaga os
    dois juntos. A quota (uso_documentos_bytes) filtra excluídos porque
    responde uma pergunta diferente ("quanto essa empresa pode subir
    agora"); este gráfico responde "quanto disco físico a plataforma usa".

    Meses sem upload repetem o valor acumulado do mês anterior — sem isso,
    uma empresa que ficou parada vários meses pareceria ter dado um salto
    instantâneo no mês seguinte.

    Retorna (labels, total_gb, series):
      labels    — ["2026-01", "2026-02", ...], eixo X comum a todas as séries
      total_gb  — acumulado da plataforma inteira, mesmo comprimento de labels
      series    — [{"empresa": nome, "valores_gb": [...]}, ...] ordenado por
                  nome, cada valores_gb com o mesmo comprimento de labels.
                  Empresa sem nenhum documento não aparece aqui.
    """
    linhas = (
        DocumentoDF.objects
        # tzinfo=UTC: no MySQL, TruncMonth no fuso local vira CONVERT_TZ, que devolve NULL
        # (e quebra o painel) quando as tabelas de fuso do servidor não estão carregadas.
        # Em UTC o Django não converte nada; para um gráfico mensal a diferença é irrelevante.
        .annotate(mes=TruncMonth("enviado_em", tzinfo=timezone.utc))
        .values("empresa_id", "empresa__nome", "mes")
        .annotate(total=Sum("tamanho_bytes"))
    )
    if not linhas:
        return [], [], []

    por_empresa = defaultdict(dict)  # {empresa_id: {mes: bytes_no_mes}}
    nomes = {}
    todos_meses = set()
    for linha in linhas:
        por_empresa[linha["empresa_id"]][linha["mes"]] = linha["total"] or 0
        nomes[linha["empresa_id"]] = linha["empresa__nome"]
        todos_meses.add(linha["mes"])

    mes_atual, mes_final = min(todos_meses), max(todos_meses)
    meses_sequencia, labels = [], []
    while mes_atual <= mes_final:
        meses_sequencia.append(mes_atual)
        labels.append(mes_atual.strftime("%Y-%m"))
        mes_atual += relativedelta(months=1)

    total_gb = [0.0] * len(meses_sequencia)
    series = []
    for empresa_id, buckets in por_empresa.items():
        acumulado = 0
        valores_gb = []
        for i, mes in enumerate(meses_sequencia):
            acumulado += buckets.get(mes, 0)
            gb = acumulado / 1024 ** 3
            valores_gb.append(round(gb, 3))
            total_gb[i] += gb
        series.append({"empresa": nomes[empresa_id], "valores_gb": valores_gb})

    series.sort(key=lambda s: s["empresa"])
    return labels, [round(v, 3) for v in total_gb], series


def resposta_download(documento):
    """Serve o conteúdo do arquivo — o único ponto que sabe como fazer isso;
    nenhuma outra view ou template deve acessar documento.arquivo.url.

    - Storage local → FileResponse lendo do disco.
    - S3 (storage com url_assinada_download) → redirect para uma URL assinada
      que expira em segundos. A view de download já validou o escopo e gravou o
      log antes de chegar aqui; a URL só existe depois disso.

    content_type fixo em application/octet-stream: nunca confiamos no
    content_type declarado pelo cliente no upload (só é guardado como metadado
    informativo), então também não o repassamos na resposta — no S3 isso vai
    dentro da própria assinatura (ResponseContentType).
    """
    gerar_url = getattr(documento.arquivo.storage, "url_assinada_download", None)
    if gerar_url is not None:
        response = HttpResponseRedirect(gerar_url(documento.arquivo.name, documento.nome_original))
        # A URL carrega a assinatura: nada de guardar em cache de navegador/proxy.
        response["Cache-Control"] = "no-store"
        response["Referrer-Policy"] = "no-referrer"
        return response

    response = FileResponse(
        documento.arquivo.open("rb"),
        as_attachment=True,
        filename=documento.nome_original,
        content_type="application/octet-stream",
    )
    response["X-Content-Type-Options"] = "nosniff"
    return response
