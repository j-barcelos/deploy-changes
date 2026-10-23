#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Instalador automatico do modulo ETL + Dashboard + Executor grafico
para o projeto Django aluguel_carrinho-sorvete.

O QUE FAZ
  1. Cria o pacote de pipeline ETL          (aluguel/etl/*.py)
  2. Cria o comando de linha de comando     (aluguel/management/commands/rodar_etl.py)
  3. Cria os endpoints de dashboard, exportacao CSV/PDF e execucao grafica
  4. Adiciona os modelos RelatorioETL / ExecucaoETL e o campo Reserva.criado_em
  5. Gera a migracao correspondente
  6. Injeta o menu + telas "Dashboard ETL" e "Executar ETL" no painel
  7. Ajusta requirements.txt, settings.py e .gitignore

GARANTIAS
  * Backup .bak_<timestamp> de TODO arquivo existente antes de alterar
  * Idempotente: rodar 2x nao duplica nada (blocos com marcadores)
  * Valida a sintaxe dos .py alterados; se falhar, restaura o backup
  * No fim lista o que exige (ou pode exigir) intervencao manual

USO
    python instalar_etl.py [CAMINHO_DO_PROJETO] [opcoes]

OPCOES
    --dry             nao grava nada, so mostra o que faria
    --sem-backup      nao cria .bak_<timestamp>
    --preservar       mantem arquivos do pacote ETL ja existentes (nao sobrescreve)
    --sem-migrate     nao roda "manage.py migrate" automaticamente
    --sem-validar     nao valida sintaxe dos .py
    --instalar-deps   roda "pip install reportlab" se faltar
"""
from __future__ import annotations

import ast
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# ─────────────────────────────── CLI ───────────────────────────────
STAMP = datetime.now().strftime("%Y%m%d_%H%M%S")
DRY = "--dry" in sys.argv
SEM_BACKUP = "--sem-backup" in sys.argv
PRESERVAR = "--preservar" in sys.argv
SEM_MIGRATE = "--sem-migrate" in sys.argv
SEM_VALIDAR = "--sem-validar" in sys.argv
INSTALAR_DEPS = "--instalar-deps" in sys.argv
ARGS = [a for a in sys.argv[1:] if not a.startswith("--")]

RAIZ = Path(".").resolve()
resultados: list[tuple[str, str, str]] = []   # (arquivo, item, status)
avisos: list[str] = []

MARCA = {
    "criado": "[novo ]",
    "atualizado": "[atual]",
    "aplicado": "[ ok  ]",
    "já aplicado": "[ ==  ]",
    "NÃO ENCONTRADO": "[ !!  ]",
    "ARQUIVO AUSENTE": "[ !!  ]",
    "IGNORADO": "[ --  ]",
    "REVERTIDO": "[ !!  ]",
}


# ────────────────────────────── helpers ────────────────────────────
def rel(p: Path) -> str:
    try:
        return str(p.resolve().relative_to(RAIZ))
    except ValueError:
        return str(p)


def ler(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def backup(p: Path) -> None:
    if DRY or SEM_BACKUP:
        return
    shutil.copy2(p, p.with_name(p.name + f".bak_{STAMP}"))


def registrar(arquivo: str, item: str, status: str) -> None:
    resultados.append((arquivo, item, status))


def encontrar_raiz(base: Path) -> Path | None:
    for p in [base, *base.parents]:
        if (p / "manage.py").exists():
            return p
    for manage in base.rglob("manage.py"):
        return manage.parent
    return None


def validar_python(texto: str) -> str | None:
    if SEM_VALIDAR:
        return None
    try:
        ast.parse(texto)
        return None
    except SyntaxError as exc:
        return f"{exc.msg} (linha {exc.lineno})"


# ─────────────────────── escrita de arquivos NOVOS ─────────────────
def gravar_novo(caminho: Path, conteudo: str, item: str) -> None:
    if caminho.exists():
        atual = ler(caminho)
        if atual == conteudo:
            registrar(rel(caminho), item, "já aplicado")
            return
        if PRESERVAR:
            registrar(rel(caminho), item, "IGNORADO")
            avisos.append(f"{rel(caminho)}: mantido como esta (--preservar). "
                          f"Compare com a versao esperada se algo falhar.")
            return
        erro = validar_python(conteudo) if caminho.suffix == ".py" else None
        if erro:
            registrar(rel(caminho), item, "REVERTIDO")
            avisos.append(f"{rel(caminho)}: conteudo novo com sintaxe invalida ({erro}).")
            return
        backup(caminho)
        if not DRY:
            caminho.write_text(conteudo, encoding="utf-8")
        registrar(rel(caminho), item, "atualizado")
    else:
        erro = validar_python(conteudo) if caminho.suffix == ".py" else None
        if erro:
            registrar(rel(caminho), item, "REVERTIDO")
            avisos.append(f"{rel(caminho)}: sintaxe invalida ({erro}).")
            return
        if not DRY:
            caminho.parent.mkdir(parents=True, exist_ok=True)
            caminho.write_text(conteudo, encoding="utf-8")
        registrar(rel(caminho), item, "criado")


# ──────────────────── patch idempotente de arquivo existente ───────
class Patch:
    """Aplica edicoes em arquivo existente de forma idempotente."""

    def __init__(self, caminho: Path, label: str):
        self.caminho = caminho
        self.label = label
        self.existe = caminho.exists()
        self.original = ler(caminho) if self.existe else ""
        self.texto = self.original
        self.itens: list[tuple[str, str]] = []

    # bloco delimitado por marcadores (substitui se ja existir)
    def bloco(self, conteudo, ini, fim, item, ancora=None, depois=True,
              ancoras_alt=None, anexar=False):
        bloco = f"{ini}\n{conteudo}\n{fim}"
        if ini in self.texto and fim in self.texto:
            padrao = re.compile(re.escape(ini) + r".*?" + re.escape(fim), re.S)
            novo = padrao.sub(lambda m: bloco, self.texto, count=1)
            self.texto = novo
            self.itens.append((item, "já aplicado" if novo == self.texto else "atualizado"))
            return
        if anexar:
            self.texto = self.texto.rstrip() + "\n\n\n" + bloco + "\n"
            self.itens.append((item, "aplicado"))
            return
        for a in [ancora] + list(ancoras_alt or []):
            if a and a in self.texto:
                if depois:
                    self.texto = self.texto.replace(a, a + "\n\n" + bloco, 1)
                else:
                    self.texto = self.texto.replace(a, bloco + "\n\n" + a, 1)
                self.itens.append((item, "aplicado"))
                return
        self.itens.append((item, "NÃO ENCONTRADO"))
        avisos.append(f"{self.label}: ancora do item '{item}' nao encontrada — inserir manualmente.")

    def garantir_linha(self, linha, ancora, item, depois=True):
        if linha.strip() in self.texto:
            self.itens.append((item, "já aplicado"))
            return
        if ancora in self.texto:
            novo = (ancora + "\n" + linha) if depois else (linha + "\n" + ancora)
            self.texto = self.texto.replace(ancora, novo, 1)
            self.itens.append((item, "aplicado"))
        else:
            self.itens.append((item, "NÃO ENCONTRADO"))
            avisos.append(f"{self.label}: ancora de '{item}' nao encontrada.")

    def garantir_texto(self, trecho, ancora, item, depois=True):
        self.garantir_linha(trecho, ancora, item, depois)

    def finalizar(self):
        if not self.existe:
            registrar(self.label, "-", "ARQUIVO AUSENTE")
            avisos.append(f"{self.label}: arquivo nao encontrado.")
            return
        if self.texto == self.original:
            for item, _ in self.itens:
                registrar(self.label, item, "já aplicado")
            return
        erro = validar_python(self.texto) if self.caminho.suffix == ".py" else None
        if erro:
            registrar(self.label, "validacao de sintaxe", "REVERTIDO")
            avisos.append(f"{self.label}: sintaxe invalida apos patch ({erro}) — arquivo restaurado.")
            if not DRY:
                self.caminho.write_text(self.original, encoding="utf-8")
            return
        backup(self.caminho)
        if not DRY:
            self.caminho.write_text(self.texto, encoding="utf-8")
        for item, status in self.itens:
            registrar(self.label, item, status if status != "aplicado" else "aplicado")


# ═══════════════════════════════════════════════════════════════════
#  CONTEUDO DOS ARQUIVOS NOVOS
# ═══════════════════════════════════════════════════════════════════

ETL_INIT = r'''"""Pacote ETL do app aluguel. Pipeline: Extract -> Transform -> Load."""
from .pipeline import executar_etl  # noqa: F401

__all__ = ["executar_etl"]
'''

ETL_EXTRACT = r'''"""ETAPA 1 - EXTRACT.

Le a camada de origem (banco) SEM aplicar regra de negocio.
Registra linhagem (fonte, registros, tempo) para auditoria do pipeline.
"""
from __future__ import annotations

import time
from datetime import date

from django.db.models import Prefetch

from ..models import Carrinho, Cliente, Reserva, ReservaProduto, Sorvete

CATALOGO_FONTES = [
    {"tabela": "aluguel_reserva", "papel": "fato",
     "descricao": "Reservas de carrinho (grao = 1 reserva/evento)",
     "campos_chave": ["id", "data_evento", "status", "valor_pedido", "descricao"],
     "campos_sensiveis": ["id_cliente"]},
    {"tabela": "aluguel_reservaproduto", "papel": "fato_detalhe",
     "descricao": "Itens de sorvete por reserva (grao = sabor x reserva)",
     "campos_chave": ["id", "id_reserva", "id_sorvete", "quantidade_escolhida"],
     "campos_sensiveis": []},
    {"tabela": "aluguel_cliente", "papel": "dimensao",
     "descricao": "Cadastro de clientes (PII)",
     "campos_chave": ["id", "nome_cliente"],
     "campos_sensiveis": ["nome_cliente", "telefone", "email", "endereco"]},
    {"tabela": "aluguel_sorvete", "papel": "dimensao",
     "descricao": "Catalogo de sabores, preco e estoque",
     "campos_chave": ["id", "nome_sorvete", "preco", "quantidade", "ativo"],
     "campos_sensiveis": []},
    {"tabela": "aluguel_carrinho", "papel": "dimensao",
     "descricao": "Frota de carrinhos e valor da diaria",
     "campos_chave": ["id", "preco_diaria", "status"],
     "campos_sensiveis": []},
]


def extrair_reservas(data_ini: date, data_fim: date, status=None):
    qs = (
        Reserva.objects
        .filter(data_evento__gte=data_ini, data_evento__lte=data_fim)
        .select_related("id_cliente", "id_carrinho")
        .prefetch_related(
            Prefetch("itens", queryset=ReservaProduto.objects.select_related("id_sorvete"))
        )
        .order_by("data_evento", "id")
    )
    if status:
        qs = qs.filter(status=status)
    return list(qs)


def extrair_reservas_completas():
    return list(
        Reserva.objects
        .select_related("id_cliente", "id_carrinho")
        .prefetch_related("itens__id_sorvete")
    )


def extrair_clientes():
    return list(Cliente.objects.all().order_by("id"))


def extrair_sabores():
    return list(Sorvete.objects.all().order_by("nome_sorvete"))


def extrair_carrinhos():
    return list(Carrinho.objects.all().order_by("id"))


def extrair_tudo(data_ini: date, data_fim: date, status=None) -> dict:
    linhagem = []

    def medir(descricao, fonte, fn, *args):
        t0 = time.perf_counter()
        dados = fn(*args)
        linhagem.append({
            "etapa": "Extract",
            "fonte": fonte,
            "descricao": descricao,
            "registros": len(dados),
            "ms": round((time.perf_counter() - t0) * 1000, 2),
        })
        return dados

    return {
        "reservas": medir("Reservas do periodo", "aluguel_reserva",
                          extrair_reservas, data_ini, data_fim, status),
        "todas_reservas": medir("Base historica de reservas", "aluguel_reserva",
                                extrair_reservas_completas),
        "clientes": medir("Clientes", "aluguel_cliente", extrair_clientes),
        "sabores": medir("Catalogo de sabores", "aluguel_sorvete", extrair_sabores),
        "carrinhos": medir("Frota", "aluguel_carrinho", extrair_carrinhos),
        "catalogo": CATALOGO_FONTES,
        "linhagem": linhagem,
    }
'''

ETL_TRANSFORM = r'''"""ETAPA 2 - TRANSFORM.

Converte o bruto em camada analitica: linhas normalizadas, KPIs, series
temporais, funil, rankings, ocupacao, outliers e qualidade de dados.
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

REGEX_CARRINHOS = re.compile(
    r"Quantidade de carrinhos(?: solicitada)?:\s*(\d+)", re.IGNORECASE
)
STATUS_LABEL = {"pendente": "Pendente", "confirmado": "Confirmado", "cancelado": "Cancelado"}
STATUS_ORDER = ["confirmado", "pendente", "cancelado"]


def quantidade_carrinhos(reserva) -> int:
    match = REGEX_CARRINHOS.search(reserva.descricao or "")
    return max(1, int(match.group(1))) if match else 1


def limpar_descricao(reserva) -> str:
    return " ".join(REGEX_CARRINHOS.sub("", reserva.descricao or "").split()).strip()


def _f(valor) -> float:
    if valor is None:
        return 0.0
    if isinstance(valor, Decimal):
        return float(valor)
    try:
        return float(valor)
    except (TypeError, ValueError):
        return 0.0


def normalizar_reserva(reserva) -> dict:
    itens = list(reserva.itens.all())
    qtd_carrinhos = quantidade_carrinhos(reserva)

    subtotal = sum(
        int(item.quantidade_escolhida or 0) * _f(item.id_sorvete.preco)
        for item in itens if item.id_sorvete
    )
    taxa_total = _f(reserva.taxa_aluguel()) * qtd_carrinhos

    lead = None
    if getattr(reserva, "criado_em", None) and reserva.data_evento:
        lead = (reserva.data_evento - reserva.criado_em.date()).days

    cliente = reserva.id_cliente

    return {
        "reserva_id": reserva.id,
        "data_evento": reserva.data_evento.isoformat() if reserva.data_evento else "",
        "status": reserva.status,
        "status_label": STATUS_LABEL.get(reserva.status, reserva.status),
        "cliente_id": reserva.id_cliente_id,
        "cliente_nome": cliente.nome_cliente if cliente else "",
        "cliente_telefone": cliente.telefone if cliente else "",
        "cliente_email": (cliente.email or "") if cliente else "",
        "cliente_endereco": cliente.endereco if cliente else "",
        "carrinho_id": reserva.id_carrinho_id,
        "quantidade_carrinhos": qtd_carrinhos,
        "qtd_itens": sum(int(item.quantidade_escolhida or 0) for item in itens),
        "sabores": ", ".join(sorted({i.id_sorvete.nome_sorvete for i in itens if i.id_sorvete})),
        "observacoes": limpar_descricao(reserva),
        "subtotal": round(subtotal, 2),
        "taxa_aluguel": round(taxa_total, 2),
        "total": round(subtotal + taxa_total, 2),
        "lead_time_dias": lead,
        "itens": [
            {
                "sorvete_id": item.id_sorvete_id,
                "sabor": item.id_sorvete.nome_sorvete if item.id_sorvete else "Sabor removido",
                "quantidade": int(item.quantidade_escolhida or 0),
                "preco_unitario": _f(item.id_sorvete.preco) if item.id_sorvete else 0.0,
                "receita": round(
                    int(item.quantidade_escolhida or 0) * _f(item.id_sorvete.preco), 2
                ) if item.id_sorvete else 0.0,
            }
            for item in itens
        ],
    }


def _chave_periodo(d: date, granularidade: str) -> str:
    if granularidade == "dia":
        return d.isoformat()
    if granularidade == "semana":
        return (d - timedelta(days=d.weekday())).isoformat()
    return f"{d.year}-{d.month:02d}"


def serie_temporal(linhas, granularidade: str) -> list:
    buckets = defaultdict(lambda: {
        "reservas": 0, "confirmadas": 0, "pendentes": 0, "canceladas": 0,
        "faturamento": 0.0, "receita_potencial": 0.0, "sorvetes": 0, "carrinhos": 0,
    })
    for l in linhas:
        if not l["data_evento"]:
            continue
        b = buckets[_chave_periodo(date.fromisoformat(l["data_evento"]), granularidade)]
        b["reservas"] += 1
        if l["status"] == "confirmado":
            b["confirmadas"] += 1
            b["faturamento"] += l["total"]
            b["sorvetes"] += l["qtd_itens"]
            b["carrinhos"] += l["quantidade_carrinhos"]
        elif l["status"] == "pendente":
            b["pendentes"] += 1
            b["receita_potencial"] += l["total"]
        else:
            b["canceladas"] += 1

    return [
        {
            "periodo": chave,
            "reservas": b["reservas"],
            "confirmadas": b["confirmadas"],
            "pendentes": b["pendentes"],
            "canceladas": b["canceladas"],
            "faturamento": round(b["faturamento"], 2),
            "receita_potencial": round(b["receita_potencial"], 2),
            "ticket_medio": round(b["faturamento"] / b["confirmadas"], 2) if b["confirmadas"] else 0.0,
            "sorvetes": b["sorvetes"],
            "carrinhos": b["carrinhos"],
        }
        for chave, b in sorted(buckets.items())
    ]


def funil_status(linhas) -> list:
    total = len(linhas) or 1
    agrupado = defaultdict(lambda: {"total": 0, "valor": 0.0})
    for l in linhas:
        agrupado[l["status"]]["total"] += 1
        agrupado[l["status"]]["valor"] += l["total"]
    return [
        {"status": s, "label": STATUS_LABEL[s], "total": agrupado[s]["total"],
         "valor": round(agrupado[s]["valor"], 2),
         "participacao": round(agrupado[s]["total"] / total * 100, 2)}
        for s in STATUS_ORDER if s in agrupado
    ]


def ranking_sabores(linhas, limite: int = 10) -> list:
    acc = defaultdict(lambda: {"quantidade": 0, "receita": 0.0, "reservas": 0})
    for l in linhas:
        if l["status"] != "confirmado":
            continue
        for item in l["itens"]:
            a = acc[item["sabor"]]
            a["quantidade"] += item["quantidade"]
            a["receita"] += item["receita"]
            a["reservas"] += 1
    total_receita = sum(v["receita"] for v in acc.values()) or 1
    ordenado = sorted(acc.items(), key=lambda kv: kv[1]["receita"], reverse=True)[:limite]
    return [
        {"sabor": nome, "quantidade": v["quantidade"], "receita": round(v["receita"], 2),
         "reservas": v["reservas"], "participacao": round(v["receita"] / total_receita * 100, 2)}
        for nome, v in ordenado
    ]


def ranking_clientes(linhas, limite: int = 10) -> list:
    acc = defaultdict(lambda: {"nome": "", "telefone": "", "reservas": 0,
                               "confirmadas": 0, "total": 0.0, "ultima": ""})
    for l in linhas:
        a = acc[l["cliente_id"]]
        a["nome"] = l["cliente_nome"]
        a["telefone"] = l["cliente_telefone"]
        a["reservas"] += 1
        if l["status"] == "confirmado":
            a["confirmadas"] += 1
            a["total"] += l["total"]
        a["ultima"] = max(a["ultima"], l["data_evento"])
    ordenado = sorted(acc.items(), key=lambda kv: kv[1]["total"], reverse=True)[:limite]
    return [
        {"cliente_id": cid, "cliente": v["nome"], "telefone": v["telefone"],
         "reservas": v["reservas"], "confirmadas": v["confirmadas"],
         "total": round(v["total"], 2),
         "ticket_medio": round(v["total"] / v["confirmadas"], 2) if v["confirmadas"] else 0.0,
         "ultima_reserva": v["ultima"]}
        for cid, v in ordenado
    ]


def kpis(linhas, total_carrinhos: int) -> dict:
    confirmadas = [l for l in linhas if l["status"] == "confirmado"]
    pendentes = [l for l in linhas if l["status"] == "pendente"]
    canceladas = [l for l in linhas if l["status"] == "cancelado"]

    faturamento = sum(l["total"] for l in confirmadas)
    clientes = {l["cliente_id"] for l in linhas}
    recorrentes = {c for c in clientes if sum(1 for l in linhas if l["cliente_id"] == c) > 1}
    lead_times = [l["lead_time_dias"] for l in linhas if l["lead_time_dias"] is not None]
    total = len(linhas) or 1
    carrinho_dias = sum(l["quantidade_carrinhos"] for l in confirmadas)

    return {
        "reservas_total": len(linhas),
        "confirmadas": len(confirmadas),
        "pendentes": len(pendentes),
        "canceladas": len(canceladas),
        "taxa_confirmacao": round(len(confirmadas) / total * 100, 2),
        "taxa_cancelamento": round(len(canceladas) / total * 100, 2),
        "faturamento_confirmado": round(faturamento, 2),
        "receita_potencial": round(sum(l["total"] for l in pendentes), 2),
        "receita_perdida": round(sum(l["total"] for l in canceladas), 2),
        "ticket_medio": round(faturamento / len(confirmadas), 2) if confirmadas else 0.0,
        "sorvetes_vendidos": sum(l["qtd_itens"] for l in confirmadas),
        "carrinho_dias": carrinho_dias,
        "valor_medio_por_carrinho": round(faturamento / carrinho_dias, 2) if carrinho_dias else 0.0,
        "clientes_unicos": len(clientes),
        "clientes_recorrentes": len(recorrentes),
        "taxa_recorrencia": round(len(recorrentes) / len(clientes) * 100, 2) if clientes else 0.0,
        "lead_time_medio_dias": round(sum(lead_times) / len(lead_times), 1) if lead_times else None,
        "total_carrinhos_frota": total_carrinhos,
        "dias_com_evento": len({l["data_evento"] for l in linhas if l["data_evento"]}),
    }


def ocupacao(linhas, total_carrinhos: int, data_ini: date, data_fim: date) -> dict:
    dias = (data_fim - data_ini).days + 1
    por_dia = defaultdict(int)
    for l in linhas:
        if l["status"] == "confirmado" and l["data_evento"]:
            por_dia[l["data_evento"]] += l["quantidade_carrinhos"]
    capacidade = total_carrinhos * dias
    usado = sum(por_dia.values())
    top = sorted(por_dia.items(), key=lambda kv: kv[1], reverse=True)[:10]
    return {
        "total_carrinhos": total_carrinhos,
        "dias_analisados": dias,
        "dias_com_reserva": len(por_dia),
        "dias_lotados": sum(1 for v in por_dia.values() if total_carrinhos and v >= total_carrinhos),
        "carrinho_dias_usados": usado,
        "capacidade_total": capacidade,
        "taxa_ocupacao": round(usado / capacidade * 100, 2) if capacidade else 0.0,
        "top_dias": [{"data": d, "carrinhos": v} for d, v in top],
    }


def outliers(linhas, limite: int = 10) -> list:
    valores = [l["total"] for l in linhas if l["status"] != "cancelado" and l["total"] > 0]
    if len(valores) < 5:
        return []
    media = sum(valores) / len(valores)
    variancia = sum((v - media) ** 2 for v in valores) / len(valores)
    desvio = variancia ** 0.5
    if desvio == 0:
        return []
    achados = sorted([l for l in linhas if l["total"] > media + 3 * desvio],
                     key=lambda l: l["total"], reverse=True)[:limite]
    return [{"reserva_id": l["reserva_id"], "cliente": l["cliente_nome"],
             "data": l["data_evento"], "total": l["total"],
             "media_periodo": round(media, 2)} for l in achados]


def qualidade_dados(bruto: dict, linhas) -> dict:
    alertas = []

    def alerta(tipo, severidade, total, exemplos, sugestao):
        if total:
            alertas.append({"tipo": tipo, "severidade": severidade, "total": total,
                            "exemplos": exemplos[:5], "sugestao": sugestao})

    reservas = bruto["reservas"]
    todas = bruto["todas_reservas"]
    sabores = bruto["sabores"]
    clientes = bruto["clientes"]

    alerta("Reserva confirmada sem carrinho vinculado", "alta",
           sum(1 for r in reservas if r.status == "confirmado" and not r.id_carrinho_id),
           [f"Reserva #{r.id}" for r in reservas if r.status == "confirmado" and not r.id_carrinho_id],
           "Preencher id_carrinho ao confirmar a reserva.")

    alerta("Reserva sem nenhum item de sorvete", "alta",
           sum(1 for r in reservas if not r.itens.all()),
           [f"Reserva #{r.id} ({r.status})" for r in reservas if not r.itens.all()],
           "Exigir ao menos 1 item em api_criar_reserva.")

    alerta("Reserva pendente com data ja vencida", "alta",
           sum(1 for r in todas if r.status == "pendente" and r.data_evento < date.today()),
           [f"Reserva #{r.id} ({r.data_evento})" for r in todas
            if r.status == "pendente" and r.data_evento < date.today()],
           "Criar rotina de expiracao automatica de pendentes vencidas.")

    alerta("Sabor com estoque negativo", "media",
           sum(1 for s in sabores if s.quantidade < 0),
           [f"{s.nome_sorvete} ({s.quantidade})" for s in sabores if s.quantidade < 0],
           "Bloquear baixa de estoque quando o saldo for insuficiente.")

    alerta("Sabor ativo com preco zero", "media",
           sum(1 for s in sabores if s.ativo and float(s.preco) <= 0),
           [s.nome_sorvete for s in sabores if s.ativo and float(s.preco) <= 0],
           "Revisar precificacao do catalogo.")

    alerta("Cliente sem telefone", "media",
           sum(1 for c in clientes if not (c.telefone or "").strip()),
           [f"Cliente #{c.id} {c.nome_cliente}" for c in clientes if not (c.telefone or "").strip()],
           "Tornar telefone obrigatorio no formulario publico.")

    telefones = {}
    for c in clientes:
        if c.telefone:
            telefones.setdefault(c.telefone, []).append(c)
    alerta("Cliente duplicado por telefone", "media",
           sum(len(v) - 1 for v in telefones.values() if len(v) > 1),
           [f"{t} -> {len(v)} cadastros" for t, v in telefones.items() if len(v) > 1],
           "Criar deduplicacao por telefone/email no cadastro.")

    alerta("Reserva confirmada com valor zerado", "media",
           sum(1 for r in reservas if r.status == "confirmado" and float(r.valor_pedido or 0) == 0),
           [f"Reserva #{r.id}" for r in reservas
            if r.status == "confirmado" and float(r.valor_pedido or 0) == 0],
           "Recalcular valor_pedido tambem ao confirmar (hoje so recalcula em pendente).")

    alerta("Item de reserva com sabor removido do catalogo", "baixa",
           sum(1 for r in reservas for i in r.itens.all() if not i.id_sorvete_id),
           [f"Reserva #{r.id}" for r in reservas if any(not i.id_sorvete_id for i in r.itens.all())],
           "Usar on_delete=PROTECT em ReservaProduto.id_sorvete.")

    alerta("Reserva duplicada (mesmo cliente + mesma data)", "baixa",
           len(linhas) - len({(l["cliente_id"], l["data_evento"]) for l in linhas}),
           [f"{l['cliente_nome']} em {l['data_evento']}" for l in linhas
            if sum(1 for x in linhas if x["cliente_id"] == l["cliente_id"]
                   and x["data_evento"] == l["data_evento"]) > 1],
           "Avaliar unique_together (id_cliente, data_evento).")

    alerta("Lead time curto (< 2 dias)", "baixa",
           sum(1 for l in linhas if l["lead_time_dias"] is not None and l["lead_time_dias"] < 2),
           [f"Reserva #{l['reserva_id']} ({l['lead_time_dias']}d)" for l in linhas
            if l["lead_time_dias"] is not None and l["lead_time_dias"] < 2],
           "Reforcar a comunicacao de antecedencia minima de 24h.")

    ordem = {"alta": 0, "media": 1, "baixa": 2}
    alertas.sort(key=lambda a: ordem[a["severidade"]])

    return {
        "total_alertas": len(alertas),
        "por_severidade": {
            "alta": sum(1 for a in alertas if a["severidade"] == "alta"),
            "media": sum(1 for a in alertas if a["severidade"] == "media"),
            "baixa": sum(1 for a in alertas if a["severidade"] == "baixa"),
        },
        "alertas": alertas,
    }


def transformar(bruto: dict, granularidade: str, data_ini: date, data_fim: date,
                limite_linhas: int = 5000) -> dict:
    linhas = sorted((normalizar_reserva(r) for r in bruto["reservas"]),
                    key=lambda l: l["data_evento"])
    total_carrinhos = sum(1 for c in bruto["carrinhos"] if c.status)

    return {
        "linhas": linhas[:limite_linhas],
        "linhas_total": len(linhas),
        "kpis": kpis(linhas, total_carrinhos),
        "serie": serie_temporal(linhas, granularidade),
        "funil_status": funil_status(linhas),
        "top_sabores": ranking_sabores(linhas),
        "top_clientes": ranking_clientes(linhas),
        "ocupacao": ocupacao(linhas, total_carrinhos, data_ini, data_fim),
        "outliers": outliers(linhas),
        "qualidade": qualidade_dados(bruto, linhas),
        "fontes": bruto["catalogo"],
        "linhagem": bruto["linhagem"],
    }
'''

ETL_LOAD = r'''"""ETAPA 3 - LOAD.

Persiste o resultado do pipeline como snapshot versionado (destino) e
registra as execucoes disparadas pela interface grafica.
"""
from __future__ import annotations

from datetime import date

from ..models import ExecucaoETL, RelatorioETL


def persistir(payload: dict, data_ini: date, data_fim: date, granularidade: str,
              duracao_ms: int, usuario=None, log=None) -> RelatorioETL:
    return RelatorioETL.objects.create(
        periodo_inicio=data_ini,
        periodo_fim=data_fim,
        granularidade=granularidade,
        total_registros=payload.get("linhas_total", 0),
        duracao_ms=duracao_ms,
        payload=payload,
        log=log or [],
        criado_por=usuario if (usuario and usuario.is_authenticated) else None,
    )


def historico(limite: int = 20) -> list:
    return [
        {
            "id": r.id,
            "periodo_inicio": r.periodo_inicio.isoformat(),
            "periodo_fim": r.periodo_fim.isoformat(),
            "granularidade": r.granularidade,
            "total_registros": r.total_registros,
            "duracao_ms": r.duracao_ms,
            "criado_em": r.criado_em.isoformat(),
            "criado_por": r.criado_por.username if r.criado_por else "sistema",
            "kpis": {
                "faturamento_confirmado": r.payload.get("kpis", {}).get("faturamento_confirmado", 0),
                "confirmadas": r.payload.get("kpis", {}).get("confirmadas", 0),
                "alertas_qualidade": r.payload.get("qualidade", {}).get("total_alertas", 0),
            },
        }
        for r in RelatorioETL.objects.select_related("criado_por")[:limite]
    ]


def carregar(snapshot_id: int):
    try:
        return RelatorioETL.objects.get(id=snapshot_id).payload
    except RelatorioETL.DoesNotExist:
        return None


def registrar_execucao(comando: str, parametros: dict, usuario=None) -> ExecucaoETL:
    return ExecucaoETL.objects.create(
        comando=comando,
        parametros=parametros,
        status="rodando",
        criado_por=usuario if (usuario and usuario.is_authenticated) else None,
    )


def historico_execucoes(limite: int = 20) -> list:
    return [
        {
            "id": e.id,
            "comando": e.comando,
            "status": e.status,
            "duracao_ms": e.duracao_ms,
            "arquivos": e.arquivos or [],
            "criado_em": e.criado_em.isoformat(),
            "criado_por": e.criado_por.username if e.criado_por else "sistema",
        }
        for e in ExecucaoETL.objects.select_related("criado_por")[:limite]
    ]
'''

ETL_EXPORTERS = r'''"""Geracao dos arquivos de saida do dashboard: CSV (Excel-BR) e PDF."""
from __future__ import annotations

import csv
import io
from datetime import datetime

TIPOS_CSV = ("linhas", "serie", "sabores", "clientes", "qualidade", "resumo")


def _fmt_moeda(v) -> str:
    return f"{float(v or 0):.2f}".replace(".", ",")


def gerar_csv(payload: dict, tipo: str = "linhas") -> str:
    if tipo not in TIPOS_CSV:
        tipo = "linhas"

    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    etl = payload.get("etl", {})
    periodo = etl.get("periodo", {})

    w.writerow(["Relatorio ETL - Oggi Sorvetes"])
    w.writerow([f"Periodo: {periodo.get('inicio','')} a {periodo.get('fim','')}"])
    w.writerow([f"Gerado em: {payload.get('gerado_em','')}"])
    w.writerow([])

    if tipo == "linhas":
        w.writerow(["Reserva", "Data do evento", "Status", "Cliente", "Telefone", "E-mail",
                    "Endereco", "Carrinhos", "Sabores", "Qtd sorvetes", "Subtotal",
                    "Taxa aluguel", "Total", "Lead time (dias)", "Observacoes"])
        for l in payload.get("linhas", []):
            w.writerow([l["reserva_id"], l["data_evento"], l["status_label"], l["cliente_nome"],
                        l["cliente_telefone"], l["cliente_email"], l["cliente_endereco"],
                        l["quantidade_carrinhos"], l["sabores"], l["qtd_itens"],
                        _fmt_moeda(l["subtotal"]), _fmt_moeda(l["taxa_aluguel"]),
                        _fmt_moeda(l["total"]), l["lead_time_dias"] or "", l["observacoes"]])

    elif tipo == "serie":
        w.writerow(["Periodo", "Reservas", "Confirmadas", "Pendentes", "Canceladas",
                    "Faturamento", "Receita potencial", "Ticket medio", "Sorvetes", "Carrinhos"])
        for s in payload.get("serie", []):
            w.writerow([s["periodo"], s["reservas"], s["confirmadas"], s["pendentes"],
                        s["canceladas"], _fmt_moeda(s["faturamento"]),
                        _fmt_moeda(s["receita_potencial"]), _fmt_moeda(s["ticket_medio"]),
                        s["sorvetes"], s["carrinhos"]])

    elif tipo == "sabores":
        w.writerow(["Sabor", "Quantidade vendida", "Receita", "Reservas", "Participacao (%)"])
        for s in payload.get("top_sabores", []):
            w.writerow([s["sabor"], s["quantidade"], _fmt_moeda(s["receita"]),
                        s["reservas"], s["participacao"]])

    elif tipo == "clientes":
        w.writerow(["Cliente", "Telefone", "Reservas", "Confirmadas", "Total gasto",
                    "Ticket medio", "Ultima reserva"])
        for c in payload.get("top_clientes", []):
            w.writerow([c["cliente"], c["telefone"], c["reservas"], c["confirmadas"],
                        _fmt_moeda(c["total"]), _fmt_moeda(c["ticket_medio"]),
                        c["ultima_reserva"]])

    elif tipo == "qualidade":
        w.writerow(["Severidade", "Tipo", "Total", "Exemplos", "Sugestao"])
        for a in payload.get("qualidade", {}).get("alertas", []):
            w.writerow([a["severidade"], a["tipo"], a["total"],
                        " | ".join(a["exemplos"]), a["sugestao"]])

    else:
        w.writerow(["Indicador", "Valor"])
        for chave, valor in payload.get("kpis", {}).items():
            w.writerow([chave, valor])
        w.writerow([])
        w.writerow(["Funil por status", "Total", "Valor"])
        for f in payload.get("funil_status", []):
            w.writerow([f["label"], f["total"], _fmt_moeda(f["valor"])])

    return "\ufeff" + buf.getvalue()


def nome_arquivo(payload: dict, tipo: str, ext: str) -> str:
    p = payload.get("etl", {}).get("periodo", {})
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    return f"etl_{tipo}_{p.get('inicio','')}_{p.get('fim','')}_{stamp}.{ext}"


def gerar_pdf(payload: dict) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (Paragraph, SimpleDocTemplate, Spacer, Table,
                                    TableStyle)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=18 * mm, bottomMargin=16 * mm,
                            leftMargin=16 * mm, rightMargin=16 * mm,
                            title="Relatorio ETL - Oggi Sorvetes")
    estilos = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=estilos["Heading1"], fontSize=17,
                        textColor=colors.HexColor("#1E1535"))
    h2 = ParagraphStyle("h2", parent=estilos["Heading2"], fontSize=12,
                        textColor=colors.HexColor("#7C5FCA"), spaceBefore=12, spaceAfter=4)
    txt = ParagraphStyle("txt", parent=estilos["BodyText"], fontSize=9, leading=12)
    mini = ParagraphStyle("mini", parent=estilos["BodyText"], fontSize=8,
                          textColor=colors.HexColor("#6B5E8A"))

    def tabela(cabecalho, linhas, larguras=None):
        t = Table([cabecalho] + linhas, colWidths=larguras, repeatRows=1)
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EDE7FF")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#1E1535")),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#E0D5F5")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#FDFBFF")]),
        ]))
        return t

    etl = payload.get("etl", {})
    periodo = etl.get("periodo", {})
    k = payload.get("kpis", {})

    story = [
        Paragraph("Relatorio ETL - Dashboard Oggi Sorvetes", h1),
        Paragraph(
            f"Periodo: <b>{periodo.get('inicio','')}</b> a <b>{periodo.get('fim','')}</b> | "
            f"Granularidade: {etl.get('granularidade','mes')} | "
            f"Gerado em {payload.get('gerado_em','')} | "
            f"Duracao: {etl.get('duracao_ms',0)} ms | Pipeline v{etl.get('versao_pipeline','1.0.0')}",
            mini),
        Spacer(1, 8),
        Paragraph("1. Indicadores-chave (KPIs)", h2),
        tabela(["Indicador", "Valor"], [
            ["Reservas no periodo", k.get("reservas_total", 0)],
            ["Confirmadas", k.get("confirmadas", 0)],
            ["Pendentes", k.get("pendentes", 0)],
            ["Canceladas", k.get("canceladas", 0)],
            ["Taxa de confirmacao (%)", f"{k.get('taxa_confirmacao',0):.2f}"],
            ["Taxa de cancelamento (%)", f"{k.get('taxa_cancelamento',0):.2f}"],
            ["Faturamento confirmado (R$)", _fmt_moeda(k.get("faturamento_confirmado"))],
            ["Receita potencial (R$)", _fmt_moeda(k.get("receita_potencial"))],
            ["Receita perdida (R$)", _fmt_moeda(k.get("receita_perdida"))],
            ["Ticket medio (R$)", _fmt_moeda(k.get("ticket_medio"))],
            ["Sorvetes vendidos", k.get("sorvetes_vendidos", 0)],
            ["Carrinho-dias", k.get("carrinho_dias", 0)],
            ["Clientes unicos", k.get("clientes_unicos", 0)],
            ["Taxa de recorrencia (%)", f"{k.get('taxa_recorrencia',0):.2f}"],
            ["Lead time medio (dias)", k.get("lead_time_medio_dias") or "n/d"],
            ["Taxa de ocupacao da frota (%)",
             f"{payload.get('ocupacao',{}).get('taxa_ocupacao',0):.2f}"],
        ], [95 * mm, 70 * mm]),
        Paragraph("2. Serie temporal", h2),
    ]

    serie = payload.get("serie", [])[:60]
    if serie:
        story.append(tabela(
            ["Periodo", "Reservas", "Conf.", "Pend.", "Canc.", "Faturamento", "Ticket medio"],
            [[s["periodo"], s["reservas"], s["confirmadas"], s["pendentes"], s["canceladas"],
              _fmt_moeda(s["faturamento"]), _fmt_moeda(s["ticket_medio"])] for s in serie]))
    else:
        story.append(Paragraph("Sem dados no periodo.", txt))

    story.append(Paragraph("3. Ranking de sabores (confirmadas)", h2))
    if payload.get("top_sabores"):
        story.append(tabela(
            ["Sabor", "Qtd", "Receita", "Reservas", "% receita"],
            [[s["sabor"], s["quantidade"], _fmt_moeda(s["receita"]), s["reservas"],
              f"{s['participacao']:.1f}%"] for s in payload["top_sabores"]],
            [70 * mm, 20 * mm, 30 * mm, 22 * mm, 23 * mm]))

    story.append(Paragraph("4. Top clientes", h2))
    if payload.get("top_clientes"):
        story.append(tabela(
            ["Cliente", "Telefone", "Reservas", "Total gasto", "Ticket medio"],
            [[c["cliente"], c["telefone"], c["reservas"], _fmt_moeda(c["total"]),
              _fmt_moeda(c["ticket_medio"])] for c in payload["top_clientes"]]))

    story.append(Paragraph("5. Funil por status", h2))
    story.append(tabela(
        ["Status", "Total", "Valor", "Participacao"],
        [[f["label"], f["total"], _fmt_moeda(f["valor"]), f"{f['participacao']:.1f}%"]
         for f in payload.get("funil_status", [])]))

    story.append(Paragraph("6. Qualidade dos dados", h2))
    alertas = payload.get("qualidade", {}).get("alertas", [])
    if alertas:
        story.append(tabela(
            ["Sev.", "Problema", "Ocorrencias", "Sugestao"],
            [[a["severidade"].upper(), a["tipo"], a["total"], a["sugestao"]] for a in alertas],
            [16 * mm, 60 * mm, 22 * mm, 67 * mm]))
    else:
        story.append(Paragraph("Nenhum alerta de qualidade encontrado.", txt))

    story.append(Paragraph("7. Linhagem e log do pipeline", h2))
    story.append(tabela(
        ["Etapa", "Fonte", "Descricao", "Registros", "ms"],
        [[e.get("etapa", ""), e.get("fonte", ""), e.get("descricao", ""),
          e.get("registros", 0), e.get("ms", 0)] for e in etl.get("etapas", [])],
        [18 * mm, 38 * mm, 78 * mm, 18 * mm, 13 * mm]))

    story.append(Spacer(1, 10))
    story.append(Paragraph(
        "Documento gerado automaticamente pelo pipeline ETL do painel administrativo.", mini))

    doc.build(story)
    return buf.getvalue()
'''

ETL_PIPELINE = r'''"""Orquestrador do pipeline ETL com log de etapas e medicao de tempo."""
from __future__ import annotations

import time
from datetime import date

from django.conf import settings
from django.utils import timezone

from . import extract, load, transform

GRANULARIDADES = ("dia", "semana", "mes")
VERSAO_PIPELINE = "1.0.0"


def _cfg(chave, padrao):
    return getattr(settings, "ETL", {}).get(chave, padrao)


def periodo_padrao() -> tuple:
    hoje = timezone.localdate()
    return date(hoje.year, 1, 1), date(hoje.year, 12, 31)


def executar_etl(data_ini=None, data_fim=None, granularidade: str = "mes",
                 status=None, persistir: bool = False, usuario=None,
                 limite_linhas=None) -> dict:
    t0 = time.perf_counter()
    ini_padrao, fim_padrao = periodo_padrao()
    data_ini = data_ini or ini_padrao
    data_fim = data_fim or fim_padrao
    if data_fim < data_ini:
        data_ini, data_fim = data_fim, data_ini
    if granularidade not in GRANULARIDADES:
        granularidade = "mes"
    if limite_linhas is None:
        limite_linhas = _cfg("LIMITE_LINHAS", 5000)

    etapas = []
    erro = None
    payload = {}

    try:
        bruto = extract.extrair_tudo(data_ini, data_fim, status)
        etapas.extend(bruto["linhagem"])

        t_t = time.perf_counter()
        payload = transform.transformar(bruto, granularidade, data_ini, data_fim, limite_linhas)
        etapas.append({
            "etapa": "Transform",
            "fonte": "camada_analitica",
            "descricao": "Normalizacao, KPIs, series, rankings, ocupacao e qualidade",
            "registros": payload["linhas_total"],
            "ms": round((time.perf_counter() - t_t) * 1000, 2),
        })
    except Exception as exc:
        erro = f"{type(exc).__name__}: {exc}"

    duracao_ms = round((time.perf_counter() - t0) * 1000, 2)
    snapshot = None

    if not erro and persistir:
        t_l = time.perf_counter()
        snapshot = load.persistir(payload, data_ini, data_fim, granularidade,
                                  duracao_ms, usuario, log=etapas)
        etapas.append({
            "etapa": "Load",
            "fonte": "aluguel_relatorioetl",
            "descricao": f"Snapshot #{snapshot.id} gravado",
            "registros": payload["linhas_total"],
            "ms": round((time.perf_counter() - t_l) * 1000, 2),
        })

    payload.setdefault("kpis", {})
    payload["etl"] = {
        "status": "erro" if erro else "ok",
        "erro": erro,
        "iniciado_em": timezone.localtime().isoformat(),
        "duracao_ms": duracao_ms,
        "periodo": {"inicio": data_ini.isoformat(), "fim": data_fim.isoformat()},
        "granularidade": granularidade,
        "filtro_status": status,
        "persistido": bool(snapshot),
        "snapshot_id": getattr(snapshot, "id", None),
        "etapas": etapas,
        "versao_pipeline": VERSAO_PIPELINE,
    }
    payload["gerado_em"] = timezone.localtime().isoformat()
    return payload
'''

API_ETL = r'''"""Endpoints do Dashboard ETL + executor grafico do comando rodar_etl."""
from __future__ import annotations

import io
import json
import time
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote

from django.conf import settings
from django.core.management import call_command
from django.http import FileResponse, HttpResponse, JsonResponse
from django.utils import timezone

from .api import _admin_required
from .etl import exporters, load
from .etl.pipeline import executar_etl, periodo_padrao

ETL_CFG = getattr(settings, "ETL", {})
EXTENSOES_PERMITIDAS = {".csv", ".pdf"}


# ─────────────────────────── helpers ───────────────────────────
def _parse_data(valor, default: date) -> date:
    if not valor:
        return default
    try:
        return datetime.strptime(str(valor)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return default


def _params(request):
    ini, fim = periodo_padrao()
    inicio = _parse_data(request.GET.get("inicio"), ini)
    termino = _parse_data(request.GET.get("fim"), fim)
    if termino < inicio:
        inicio, termino = termino, inicio

    granularidade = request.GET.get("granularidade", "mes")
    if granularidade not in ("dia", "semana", "mes"):
        granularidade = "mes"

    status = request.GET.get("status") or None
    if status not in ("pendente", "confirmado", "cancelado"):
        status = None

    return inicio, termino, granularidade, status


def _base_dir() -> Path:
    return Path(settings.BASE_DIR).resolve()


def _resolver_caminho(caminho: str) -> Path:
    p = Path(str(caminho)).expanduser()
    if not p.is_absolute():
        p = _base_dir() / p
    return p.resolve()


def _dentro_do_projeto(p: Path) -> bool:
    try:
        p.relative_to(_base_dir())
        return True
    except ValueError:
        return False


def _info_arquivo(p: Path) -> dict:
    return {
        "nome": p.name,
        "caminho": str(p),
        "relativo": str(p.relative_to(_base_dir())) if _dentro_do_projeto(p) else str(p),
        "bytes": p.stat().st_size,
        "url": "/api/etl/download/?caminho=" + quote(
            str(p.relative_to(_base_dir())) if _dentro_do_projeto(p) else str(p)
        ),
    }


def _nao_autorizado():
    return JsonResponse({"erro": "Nao autorizado."}, status=401)


def _montar_comando(kwargs: dict) -> str:
    partes = ["python manage.py rodar_etl",
              f"--inicio {kwargs['inicio']}",
              f"--fim {kwargs['fim']}",
              f"--granularidade {kwargs['granularidade']}"]
    if kwargs.get("status"):
        partes.append(f"--status {kwargs['status']}")
    if kwargs.get("sem_persistir"):
        partes.append("--sem-persistir")
    if kwargs.get("exportar_csv"):
        partes.append(f'--exportar-csv "{kwargs["exportar_csv"]}"')
    if kwargs.get("exportar_pdf"):
        partes.append(f'--exportar-pdf "{kwargs["exportar_pdf"]}"')
    return " ".join(partes)


# ─────────────────────────── leitura ───────────────────────────
def api_etl_dashboard(request):
    if not _admin_required(request):
        return _nao_autorizado()
    inicio, fim, granularidade, status = _params(request)
    payload = executar_etl(inicio, fim, granularidade, status,
                           persistir=False, usuario=request.user)
    payload["historico"] = load.historico(10)
    return JsonResponse(payload)


def api_etl_executar(request):
    if not _admin_required(request):
        return _nao_autorizado()
    if request.method != "POST":
        return JsonResponse({"erro": "Metodo nao permitido."}, status=405)

    corpo = {}
    if request.body:
        try:
            corpo = json.loads(request.body)
        except json.JSONDecodeError:
            corpo = {}

    inicio, fim, granularidade, status = _params(request)
    inicio = _parse_data(corpo.get("inicio"), inicio)
    fim = _parse_data(corpo.get("fim"), fim)

    payload = executar_etl(inicio, fim, granularidade, status,
                           persistir=True, usuario=request.user)
    payload["historico"] = load.historico(10)
    codigo = 200 if payload["etl"]["status"] == "ok" else 500
    return JsonResponse(payload, status=codigo)


def api_etl_historico(request):
    if not _admin_required(request):
        return _nao_autorizado()
    return JsonResponse(load.historico(30), safe=False)


def api_etl_execucoes(request):
    if not _admin_required(request):
        return _nao_autorizado()
    return JsonResponse(load.historico_execucoes(30), safe=False)


def api_etl_snapshot(request, snapshot_id: int):
    if not _admin_required(request):
        return _nao_autorizado()
    payload = load.carregar(snapshot_id)
    if payload is None:
        return JsonResponse({"erro": "Snapshot nao encontrado."}, status=404)
    return JsonResponse(payload)


# ────────────────────── executor grafico ──────────────────────
def api_etl_executar_comando(request):
    """Roda o management command rodar_etl como se fosse pela CLI."""
    if not _admin_required(request):
        return _nao_autorizado()
    if request.method != "POST":
        return JsonResponse({"erro": "Metodo nao permitido."}, status=405)

    try:
        corpo = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        corpo = {}

    ini_padrao, fim_padrao = periodo_padrao()
    inicio = _parse_data(corpo.get("inicio"), ini_padrao)
    fim = _parse_data(corpo.get("fim"), fim_padrao)
    if fim < inicio:
        inicio, fim = fim, inicio

    granularidade = corpo.get("granularidade") or "mes"
    if granularidade not in ("dia", "semana", "mes"):
        granularidade = "mes"

    status = corpo.get("status") or None
    if status not in ("pendente", "confirmado", "cancelado"):
        status = None

    persistir = bool(corpo.get("persistir", True))

    csv_bruto = (corpo.get("exportar_csv") or "").strip()
    pdf_bruto = (corpo.get("exportar_pdf") or "").strip()
    if csv_bruto and not csv_bruto.lower().endswith(".csv"):
        csv_bruto += ".csv"
    if pdf_bruto and not pdf_bruto.lower().endswith(".pdf"):
        pdf_bruto += ".pdf"

    kwargs = {
        "inicio": inicio.isoformat(),
        "fim": fim.isoformat(),
        "granularidade": granularidade,
        "status": status,
        "sem_persistir": not persistir,
        "exportar_csv": csv_bruto or None,
        "exportar_pdf": pdf_bruto or None,
    }
    comando = _montar_comando(kwargs)

    parametros = {**kwargs, "inicio": inicio.isoformat(), "fim": fim.isoformat()}
    execucao = load.registrar_execucao(comando, parametros, request.user)

    buf = io.StringIO()
    t0 = time.perf_counter()
    erro = None
    try:
        call_command("rodar_etl", stdout=buf, stderr=buf, **kwargs)
    except Exception as exc:
        erro = f"{type(exc).__name__}: {exc}"

    duracao_ms = round((time.perf_counter() - t0) * 1000)
    saida = buf.getvalue().strip()

    arquivos = []
    for bruto in (csv_bruto, pdf_bruto):
        if not bruto:
            continue
        p = _resolver_caminho(bruto)
        if p.exists() and p.is_file():
            arquivos.append(_info_arquivo(p))

    if erro:
        saida = (saida + "\n\n" + erro).strip()

    execucao.status = "erro" if erro else "ok"
    execucao.saida = saida
    execucao.duracao_ms = duracao_ms
    execucao.arquivos = arquivos
    execucao.save()

    return JsonResponse({
        "status": execucao.status,
        "comando": comando,
        "saida": saida or "(sem saida)",
        "duracao_ms": duracao_ms,
        "arquivos": arquivos,
        "execucao_id": execucao.id,
        "gerado_em": timezone.localtime().isoformat(),
    })


def api_etl_download(request):
    if not _admin_required(request):
        return _nao_autorizado()

    caminho = request.GET.get("caminho", "").strip()
    if not caminho:
        return JsonResponse({"erro": "Informe o parametro caminho."}, status=400)

    p = _resolver_caminho(caminho)
    if not p.exists() or not p.is_file():
        return JsonResponse({"erro": f"Arquivo nao encontrado: {p}"}, status=404)
    if p.suffix.lower() not in EXTENSOES_PERMITIDAS:
        return JsonResponse({"erro": "Extensao nao permitida (use .csv ou .pdf)."}, status=400)

    return FileResponse(open(p, "rb"), as_attachment=True, filename=p.name)


# ───────────────────────── exportacao ─────────────────────────
def _payload_para_export(request):
    inicio, fim, granularidade, status = _params(request)
    return executar_etl(inicio, fim, granularidade, status, persistir=False,
                        usuario=request.user)


def api_etl_export_csv(request):
    if not _admin_required(request):
        return _nao_autorizado()

    payload = _payload_para_export(request)
    tipo = request.GET.get("tipo", "linhas")
    conteudo = exporters.gerar_csv(payload, tipo)

    resposta = HttpResponse(conteudo, content_type="text/csv; charset=utf-8")
    resposta["Content-Disposition"] = (
        f'attachment; filename="{exporters.nome_arquivo(payload, tipo, "csv")}"'
    )
    return resposta


def api_etl_export_pdf(request):
    if not _admin_required(request):
        return _nao_autorizado()

    payload = _payload_para_export(request)
    try:
        conteudo = exporters.gerar_pdf(payload)
    except ImportError:
        return JsonResponse(
            {"erro": "Instale o reportlab: pip install reportlab"}, status=500)

    resposta = HttpResponse(conteudo, content_type="application/pdf")
    resposta["Content-Disposition"] = (
        f'attachment; filename="{exporters.nome_arquivo(payload, "relatorio", "pdf")}"'
    )
    return resposta
'''

CMD_INIT = r'''# Pacote de comandos de gerenciamento do app aluguel.
'''

CMD_RODAR_ETL = r'''"""Executa o pipeline ETL pela linha de comando (cron / Task Scheduler).

Exemplos:
    python manage.py rodar_etl
    python manage.py rodar_etl --inicio 2026-01-01 --fim 2026-12-31 --granularidade mes
    python manage.py rodar_etl --exportar-csv relatorios/etl.csv --exportar-pdf relatorios/etl.pdf
    python manage.py rodar_etl --sem-persistir
"""
from datetime import datetime
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from aluguel.etl import exporters
from aluguel.etl.pipeline import executar_etl


def _data(valor):
    try:
        return datetime.strptime(valor, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        raise CommandError(f"Data invalida: {valor} (use AAAA-MM-DD)")


class Command(BaseCommand):
    help = "Executa o pipeline ETL e opcionalmente grava snapshot / exporta relatorios."

    def add_arguments(self, parser):
        parser.add_argument("--inicio", help="Data inicial AAAA-MM-DD")
        parser.add_argument("--fim", help="Data final AAAA-MM-DD")
        parser.add_argument("--granularidade", default="mes",
                            choices=["dia", "semana", "mes"])
        parser.add_argument("--status", choices=["pendente", "confirmado", "cancelado"])
        parser.add_argument("--sem-persistir", action="store_true",
                            help="Nao grava snapshot no banco")
        parser.add_argument("--exportar-csv", help="Caminho do arquivo CSV de saida")
        parser.add_argument("--exportar-pdf", help="Caminho do arquivo PDF de saida")
        parser.add_argument("--limite-linhas", type=int, default=None)

    def handle(self, *args, **opts):
        payload = executar_etl(
            data_ini=_data(opts["inicio"]) if opts["inicio"] else None,
            data_fim=_data(opts["fim"]) if opts["fim"] else None,
            granularidade=opts["granularidade"],
            status=opts["status"],
            persistir=not opts["sem_persistir"],
            limite_linhas=opts["limite_linhas"],
        )

        etl = payload["etl"]
        self.stdout.write(f"Pipeline v{etl['versao_pipeline']} | "
                          f"periodo {etl['periodo']['inicio']} a {etl['periodo']['fim']}")
        for etapa in etl["etapas"]:
            self.stdout.write(
                f"  [{etapa['etapa']}] {etapa['descricao']}: "
                f"{etapa['registros']} registros em {etapa['ms']} ms"
            )

        k = payload["kpis"]
        self.stdout.write(self.style.SUCCESS(
            f"ETL {etl['status']} em {etl['duracao_ms']} ms | "
            f"reservas={k['reservas_total']} confirmadas={k['confirmadas']} "
            f"faturamento=R$ {k['faturamento_confirmado']:.2f} "
            f"alertas_qualidade={payload['qualidade']['total_alertas']}"
        ))

        if etl.get("snapshot_id"):
            self.stdout.write(f"Snapshot gravado: #{etl['snapshot_id']}")

        if opts["exportar_csv"]:
            destino = Path(opts["exportar_csv"])
            destino.parent.mkdir(parents=True, exist_ok=True)
            destino.write_text(exporters.gerar_csv(payload, "linhas"), encoding="utf-8")
            self.stdout.write(f"CSV gerado: {destino}")

        if opts["exportar_pdf"]:
            destino = Path(opts["exportar_pdf"])
            destino.parent.mkdir(parents=True, exist_ok=True)
            destino.write_bytes(exporters.gerar_pdf(payload))
            self.stdout.write(f"PDF gerado: {destino}")

        if etl["status"] == "erro":
            raise CommandError(etl["erro"] or "Falha no pipeline ETL.")
'''

# ── blocos injetados em arquivos existentes ────────────────────────
MODELS_BLOCO = r'''class RelatorioETL(models.Model):
    """Snapshot versionado do pipeline ETL (camada de destino / data mart)."""

    PERIODO_CHOICES = [("dia", "Diario"), ("semana", "Semanal"), ("mes", "Mensal")]

    periodo_inicio = models.DateField()
    periodo_fim = models.DateField()
    granularidade = models.CharField(max_length=10, choices=PERIODO_CHOICES, default="mes")
    total_registros = models.PositiveIntegerField(default=0)
    duracao_ms = models.PositiveIntegerField(default=0)
    payload = models.JSONField(default=dict)
    log = models.JSONField(default=list, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )

    class Meta:
        ordering = ["-criado_em"]
        verbose_name = "Relatorio ETL"
        verbose_name_plural = "Relatorios ETL"

    def __str__(self):
        return f"ETL {self.periodo_inicio} a {self.periodo_fim}"


class ExecucaoETL(models.Model):
    """Historico das execucoes disparadas pela interface grafica / CLI."""

    STATUS_CHOICES = [("rodando", "Rodando"), ("ok", "Sucesso"), ("erro", "Erro")]

    comando = models.TextField()
    parametros = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="rodando")
    saida = models.TextField(blank=True, default="")
    duracao_ms = models.PositiveIntegerField(default=0)
    arquivos = models.JSONField(default=list, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )

    class Meta:
        ordering = ["-criado_em"]
        verbose_name = "Execucao ETL"
        verbose_name_plural = "Execucoes ETL"

    def __str__(self):
        return f"Execucao {self.id} ({self.status})"
'''

SETTINGS_BLOCO = r'''# Configuracao do modulo ETL (ajuste conforme a operacao)
ETL = {
    # Quantas linhas normalizadas o dashboard/CSV carrega por execucao
    "LIMITE_LINHAS": 5000,
    # Pasta padrao dos relatorios exportados
    "PASTA_RELATORIOS": BASE_DIR / "relatorios",
    # Permite exportar para caminhos absolutos fora do projeto
    "PERMITIR_CAMINHO_ABSOLUTO": False,
    # Habilita o botao de PDF (requer reportlab instalado)
    "PDF_HABILITADO": True,
}
'''

URLS_ROTAS = r'''    # --- ETL / Dashboard -------------------------------------------------
    path('api/etl/dashboard/', api_etl.api_etl_dashboard, name='api_etl_dashboard'),
    path('api/etl/executar/', api_etl.api_etl_executar, name='api_etl_executar'),
    path('api/etl/executar-comando/', api_etl.api_etl_executar_comando,
         name='api_etl_executar_comando'),
    path('api/etl/execucoes/', api_etl.api_etl_execucoes, name='api_etl_execucoes'),
    path('api/etl/historico/', api_etl.api_etl_historico, name='api_etl_historico'),
    path('api/etl/snapshot/<int:snapshot_id>/', api_etl.api_etl_snapshot,
         name='api_etl_snapshot'),
    path('api/etl/export/csv/', api_etl.api_etl_export_csv, name='api_etl_export_csv'),
    path('api/etl/export/pdf/', api_etl.api_etl_export_pdf, name='api_etl_export_pdf'),
    path('api/etl/download/', api_etl.api_etl_download, name='api_etl_download'),
'''

HTML_NAV = r'''    <button class="nav-item" onclick="setView('dashboard',this)">
      <span class="nav-icon">&#128202;</span> Dashboard ETL
    </button>
    <button class="nav-item" onclick="setView('executar',this)">
      <span class="nav-icon">&#9881;&#65039;</span> Executar ETL
    </button>
'''

HTML_CHARTJS = r'''<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
'''

HTML_VIEWS = r'''    <!-- ============ VIEW: DASHBOARD ETL ============ -->
    <div id="view-dashboard" class="view-section">

      <div class="dash-filtros">
        <div class="dash-campo">
          <label class="form-label">Inicio</label>
          <input type="date" class="form-control" id="dashInicio">
        </div>
        <div class="dash-campo">
          <label class="form-label">Fim</label>
          <input type="date" class="form-control" id="dashFim">
        </div>
        <div class="dash-campo">
          <label class="form-label">Granularidade</label>
          <select class="form-control" id="dashGranularidade">
            <option value="dia">Diaria</option>
            <option value="semana">Semanal</option>
            <option value="mes" selected>Mensal</option>
          </select>
        </div>
        <div class="dash-campo">
          <label class="form-label">Status</label>
          <select class="form-control" id="dashStatus">
            <option value="">Todos</option>
            <option value="confirmado">Confirmadas</option>
            <option value="pendente">Pendentes</option>
            <option value="cancelado">Canceladas</option>
          </select>
        </div>
        <div class="dash-campo">
          <label class="form-label">Exportar CSV</label>
          <select class="form-control" id="dashTipoCsv">
            <option value="linhas">Reservas detalhadas</option>
            <option value="serie">Serie temporal</option>
            <option value="sabores">Ranking de sabores</option>
            <option value="clientes">Top clientes</option>
            <option value="qualidade">Qualidade dos dados</option>
            <option value="resumo">Resumo de KPIs</option>
          </select>
        </div>
        <div class="dash-botoes">
          <button class="btn btn-primary btn-sm" onclick="carregarDashboard()">Consultar</button>
          <button class="btn btn-success btn-sm" onclick="rodarEtlDashboard()">Rodar ETL</button>
          <button class="btn btn-outline btn-sm" onclick="exportarDashboard('csv')">CSV</button>
          <button class="btn btn-outline btn-sm" onclick="exportarDashboard('pdf')">PDF</button>
        </div>
      </div>

      <div class="etl-status" id="dashEtlStatus">
        <span class="spinner"></span> Carregando camada analitica...
      </div>

      <div class="kpi-grid" id="dashKpis"></div>

      <div class="chart-grid">
        <div class="chart-card chart-wide">
          <div class="panel-header"><span class="panel-title">Faturamento x Reservas por periodo</span></div>
          <div class="chart-body"><canvas id="chartSerie"></canvas></div>
        </div>
        <div class="chart-card">
          <div class="panel-header"><span class="panel-title">Funil por status</span></div>
          <div class="chart-body"><canvas id="chartStatus"></canvas></div>
        </div>
        <div class="chart-card">
          <div class="panel-header"><span class="panel-title">Top sabores por receita</span></div>
          <div class="chart-body"><canvas id="chartSabores"></canvas></div>
        </div>
        <div class="chart-card chart-wide">
          <div class="panel-header"><span class="panel-title">Carrinho-dias confirmados</span></div>
          <div class="chart-body"><canvas id="chartOcupacao"></canvas></div>
        </div>
      </div>

      <div class="dash-duas-colunas">
        <div class="panel">
          <div class="panel-header"><span class="panel-title">Top clientes</span></div>
          <div style="overflow-x:auto">
            <table>
              <thead><tr><th>Cliente</th><th>Telefone</th><th>Reservas</th><th>Total</th><th>Ticket medio</th></tr></thead>
              <tbody id="dashTopClientes"></tbody>
            </table>
          </div>
        </div>
        <div class="panel">
          <div class="panel-header"><span class="panel-title">Ranking de sabores</span></div>
          <div style="overflow-x:auto">
            <table>
              <thead><tr><th>Sabor</th><th>Qtd</th><th>Receita</th><th>%</th></tr></thead>
              <tbody id="dashTopSabores"></tbody>
            </table>
          </div>
        </div>
      </div>

      <div class="panel" style="margin-top:20px">
        <div class="panel-header">
          <span class="panel-title">Qualidade dos dados</span>
          <span id="dashQualidadeResumo" style="font-size:12px;color:var(--mid);font-weight:700"></span>
        </div>
        <div style="overflow-x:auto">
          <table>
            <thead><tr><th>Severidade</th><th>Problema</th><th>Ocorrencias</th><th>Exemplos</th><th>Sugestao</th></tr></thead>
            <tbody id="dashQualidade"></tbody>
          </table>
        </div>
      </div>

      <div class="dash-duas-colunas" style="margin-top:20px">
        <div class="panel">
          <div class="panel-header"><span class="panel-title">Linhagem e log do pipeline</span></div>
          <div class="panel-body" id="dashLinhagem"></div>
        </div>
        <div class="panel">
          <div class="panel-header"><span class="panel-title">Historico de snapshots</span></div>
          <div style="overflow-x:auto">
            <table>
              <thead><tr><th>#</th><th>Periodo</th><th>Registros</th><th>Faturamento</th><th>Alertas</th><th>Quando</th><th></th></tr></thead>
              <tbody id="dashHistorico"></tbody>
            </table>
          </div>
        </div>
      </div>

      <div class="panel" style="margin-top:20px">
        <div class="panel-header"><span class="panel-title">Catalogo de fontes (data catalog)</span></div>
        <div style="overflow-x:auto">
          <table>
            <thead><tr><th>Tabela</th><th>Papel</th><th>Descricao</th><th>Campos-chave</th><th>Campos sensiveis</th></tr></thead>
            <tbody id="dashFontes"></tbody>
          </table>
        </div>
      </div>

    </div>

    <!-- ============ VIEW: EXECUTAR ETL (interface para leigos) ============ -->
    <div id="view-executar" class="view-section">

      <div class="max-info">
        <span>&#9881;&#65039;</span>
        <span>Esta tela executa exatamente o comando <strong>python manage.py rodar_etl</strong>.
        Nada e alterado nos dados de reservas: o ETL apenas le, calcula e (se voce marcar)
        grava um snapshot e gera os arquivos CSV/PDF.</span>
      </div>
      <div style="height:12px"></div>

      <div class="dash-filtros">
        <div class="dash-campo">
          <label class="form-label">Data inicial</label>
          <input type="date" class="form-control" id="runInicio">
        </div>
        <div class="dash-campo">
          <label class="form-label">Data final</label>
          <input type="date" class="form-control" id="runFim">
        </div>
        <div class="dash-campo">
          <label class="form-label">Granularidade</label>
          <select class="form-control" id="runGranularidade">
            <option value="dia">Diaria</option>
            <option value="semana">Semanal</option>
            <option value="mes" selected>Mensal</option>
          </select>
        </div>
        <div class="dash-campo">
          <label class="form-label">Status (opcional)</label>
          <select class="form-control" id="runStatus">
            <option value="">Todos</option>
            <option value="confirmado">Somente confirmadas</option>
            <option value="pendente">Somente pendentes</option>
            <option value="cancelado">Somente canceladas</option>
          </select>
        </div>
      </div>

      <div class="dash-filtros">
        <label class="run-check">
          <input type="checkbox" id="runPersistir" checked onchange="runComandoPreview()">
          Gravar snapshot no banco
        </label>
        <label class="run-check">
          <input type="checkbox" id="runCsv" onchange="runComandoPreview()">
          Exportar CSV
        </label>
        <div class="dash-campo" style="min-width:230px">
          <input type="text" class="form-control" id="runCsvPath" placeholder="relatorios/etl.csv"
                 oninput="runComandoPreview()">
        </div>
        <label class="run-check">
          <input type="checkbox" id="runPdf" onchange="runComandoPreview()">
          Exportar PDF
        </label>
        <div class="dash-campo" style="min-width:230px">
          <input type="text" class="form-control" id="runPdfPath" placeholder="relatorios/etl.pdf"
                 oninput="runComandoPreview()">
        </div>
      </div>

      <div class="dash-filtros">
        <div class="dash-botoes" style="margin-left:0">
          <button class="btn btn-primary btn-sm" onclick="runExecutar()" id="runBtn">Executar ETL</button>
          <button class="btn btn-outline btn-sm" onclick="runLimparConsole()">Limpar saida</button>
          <button class="btn btn-outline btn-sm" onclick="runAtualizarHistorico()">Atualizar historico</button>
        </div>
        <div class="dash-botoes">
          <span class="run-preset-label">Atalhos:</span>
          <button class="btn btn-outline btn-sm" onclick="runPreset('mes')">Mes atual</button>
          <button class="btn btn-outline btn-sm" onclick="runPreset('ano')">Ano inteiro</button>
          <button class="btn btn-outline btn-sm" onclick="runPreset('90')">Ultimos 90 dias</button>
          <button class="btn btn-outline btn-sm" onclick="runPreset('consulta')">So consultar</button>
        </div>
      </div>

      <div class="panel" style="margin-top:18px">
        <div class="panel-header"><span class="panel-title">Comando equivalente (CLI)</span></div>
        <div class="panel-body"><pre class="run-comando" id="runComando"></pre></div>
      </div>

      <div class="panel" style="margin-top:16px">
        <div class="panel-header">
          <span class="panel-title">Saida da execucao</span>
          <span id="runStatusBadge" class="run-badge"></span>
        </div>
        <div class="panel-body">
          <pre class="run-console" id="runConsole">Pronto. Clique em "Executar ETL" para rodar o pipeline.</pre>
        </div>
      </div>

      <div class="panel" style="margin-top:16px">
        <div class="panel-header"><span class="panel-title">Arquivos gerados</span></div>
        <div class="panel-body" id="runArquivos">
          <p class="run-vazio">Nenhum arquivo gerado ainda.</p>
        </div>
      </div>

      <div class="panel" style="margin-top:16px">
        <div class="panel-header"><span class="panel-title">Historico de execucoes</span></div>
        <div style="overflow-x:auto">
          <table>
            <thead><tr><th>#</th><th>Quando</th><th>Por</th><th>Comando</th><th>Status</th><th>Duracao</th><th>Arquivos</th></tr></thead>
            <tbody id="runHistorico"></tbody>
          </table>
        </div>
      </div>

    </div>
'''

CSS_BLOCO = r'''/* ===================== DASHBOARD ETL ===================== */
.dash-filtros {
  background: white; border-radius: var(--radius); box-shadow: var(--shadow);
  padding: 16px 18px; margin-bottom: 18px;
  display: flex; gap: 12px; flex-wrap: wrap; align-items: flex-end;
}
.dash-campo { display: flex; flex-direction: column; gap: 4px; min-width: 140px; }
.dash-campo .form-control { padding: 8px 12px; font-size: 13px; }
.dash-botoes { display: flex; gap: 8px; flex-wrap: wrap; margin-left: auto; align-items: center; }

.etl-status {
  background: linear-gradient(90deg, #1E1535, #2D1F55); color: #fff;
  border-radius: var(--radius-sm); padding: 12px 18px; margin-bottom: 18px;
  font-size: 13px; font-weight: 600; display: flex; align-items: center; gap: 10px;
  flex-wrap: wrap;
}
.etl-status.ok { background: linear-gradient(90deg, #14532d, #1f7a4d); }
.etl-status.erro { background: linear-gradient(90deg, #7a1f1f, #b03030); }
.etl-status .tag {
  background: rgba(255,255,255,.16); padding: 3px 10px; border-radius: 99px;
  font-size: 11px; font-weight: 800; letter-spacing: .04em;
}

.kpi-grid {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 14px; margin-bottom: 20px;
}
.kpi-card {
  background: white; border-radius: var(--radius); padding: 16px 18px;
  box-shadow: var(--shadow); border-left: 4px solid var(--lavender);
  display: flex; flex-direction: column; gap: 4px;
}
.kpi-card.good { border-left-color: var(--mint); }
.kpi-card.warn { border-left-color: var(--yellow); }
.kpi-card.bad  { border-left-color: var(--red); }
.kpi-card .kpi-label {
  font-size: 11px; font-weight: 800; text-transform: uppercase;
  letter-spacing: .05em; color: var(--mid);
}
.kpi-card .kpi-value {
  font-family: 'Syne', sans-serif; font-size: 24px; font-weight: 800; color: var(--dark);
}
.kpi-card .kpi-sub { font-size: 11px; color: var(--light-text); font-weight: 600; }

.chart-grid {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
  gap: 16px; margin-bottom: 20px;
}
.chart-card {
  background: white; border-radius: var(--radius); box-shadow: var(--shadow);
  overflow: hidden;
}
.chart-card.chart-wide { grid-column: span 2; }
@media (max-width: 900px) { .chart-card.chart-wide { grid-column: span 1; } }
.chart-body { padding: 14px 16px 18px; height: 260px; position: relative; }

.dash-duas-colunas { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
@media (max-width: 1000px) { .dash-duas-colunas { grid-template-columns: 1fr; } }

.sev { padding: 2px 9px; border-radius: 99px; font-size: 10px; font-weight: 800; text-transform: uppercase; }
.sev.alta  { background: #FFE8E8; color: #9A2828; }
.sev.media { background: #FEF3C7; color: #92400E; }
.sev.baixa { background: #EDE7FF; color: #5B4A8A; }

.log-etapa {
  display: flex; align-items: center; gap: 10px; padding: 9px 12px;
  border-radius: 10px; background: var(--lavender-xl); font-size: 12px;
  margin-bottom: 6px;
}
.log-etapa .etapa-nome {
  font-family: 'Syne', sans-serif; font-weight: 800; font-size: 11px;
  background: var(--lavender); color: white; padding: 2px 8px; border-radius: 6px;
}
.log-etapa .etapa-meta { margin-left: auto; color: var(--mid); font-weight: 700; font-size: 11px; }

/* ===================== EXECUTOR ETL ===================== */
.run-check {
  display: flex; align-items: center; gap: 8px;
  font-size: 13px; font-weight: 700; color: var(--mid);
  padding-bottom: 8px;
}
.run-check input { transform: scale(1.15); accent-color: var(--lavender); cursor: pointer; }
.run-preset-label { font-size: 12px; font-weight: 800; color: var(--mid); }

.run-comando {
  background: #1E1535; color: #B9A6FF; border-radius: 10px;
  padding: 12px 14px; font-family: 'Courier New', monospace;
  font-size: 12px; white-space: pre-wrap; word-break: break-word; margin: 0;
}
.run-console {
  background: #10131a; color: #C8F7D4; border-radius: 10px;
  padding: 14px; font-family: 'Courier New', monospace;
  font-size: 12px; line-height: 1.55; white-space: pre-wrap; word-break: break-word;
  max-height: 340px; overflow-y: auto; margin: 0;
}
.run-badge {
  font-size: 11px; font-weight: 800; text-transform: uppercase;
  padding: 3px 10px; border-radius: 99px; background: var(--lavender-xl); color: var(--mid);
}
.run-vazio { font-size: 13px; color: var(--mid); margin: 0; }
.run-arquivo {
  display: flex; align-items: center; gap: 10px; padding: 10px 12px;
  background: var(--lavender-xl); border-radius: 10px; margin-bottom: 6px; font-size: 13px;
}
.run-arquivo .run-arquivo-nome { font-weight: 800; }
.run-arquivo .run-arquivo-meta { color: var(--mid); font-size: 11px; margin-left: auto; }
'''

JS_BLOCO = r'''/* =====================================================================
   DASHBOARD ETL + EXECUTOR GRAFICO
   ===================================================================== */

const dash = { dados: null, carregando: false, charts: {} };
const run = { executando: false };

function dashPeriodoPadrao() {
  const hoje = new Date();
  return {
    inicio: `${hoje.getFullYear()}-01-01`,
    fim: `${hoje.getFullYear()}-12-31`,
  };
}

function dashQuery() {
  const p = dashPeriodoPadrao();
  const inicio = document.getElementById('dashInicio')?.value || p.inicio;
  const fim = document.getElementById('dashFim')?.value || p.fim;
  const gran = document.getElementById('dashGranularidade')?.value || 'mes';
  const status = document.getElementById('dashStatus')?.value || '';
  return `inicio=${inicio}&fim=${fim}&granularidade=${gran}` + (status ? `&status=${status}` : '');
}

function dashPreencherFiltros() {
  const p = dashPeriodoPadrao();
  const i = document.getElementById('dashInicio');
  const f = document.getElementById('dashFim');
  if (i && !i.value) i.value = p.inicio;
  if (f && !f.value) f.value = p.fim;
}

async function carregarDashboard() {
  if (dash.carregando) return;
  dash.carregando = true;
  dashPreencherFiltros();

  const el = document.getElementById('dashEtlStatus');
  el.className = 'etl-status';
  el.innerHTML = '<span class="spinner"></span> Executando pipeline ETL...';

  try {
    const dados = await apiFetch(`/api/etl/dashboard/?${dashQuery()}`);
    dash.dados = dados;
    renderDashboard(dados);
  } catch (e) {
    el.className = 'etl-status erro';
    el.innerHTML = `Falha no pipeline: ${e.message}`;
  } finally {
    dash.carregando = false;
  }
}

async function rodarEtlDashboard() {
  const el = document.getElementById('dashEtlStatus');
  el.className = 'etl-status';
  el.innerHTML = '<span class="spinner"></span> Executando Extract, Transform e Load...';

  try {
    const dados = await apiFetch(`/api/etl/executar/?${dashQuery()}`, {
      method: 'POST',
      body: JSON.stringify({}),
    });
    dash.dados = dados;
    renderDashboard(dados);
    showToast(`Snapshot #${dados.etl.snapshot_id} gravado em ${dados.etl.duracao_ms} ms`, 'success');
  } catch (e) {
    el.className = 'etl-status erro';
    el.innerHTML = `Erro: ${e.message}`;
    showToast('Erro ao executar ETL: ' + e.message, 'error');
  }
}

function exportarDashboard(formato) {
  let url = `/api/etl/export/${formato}/?${dashQuery()}`;
  if (formato === 'csv') {
    const tipo = document.getElementById('dashTipoCsv')?.value || 'linhas';
    url += `&tipo=${tipo}`;
  }
  window.location.href = url;
  showToast(`Gerando ${formato.toUpperCase()}...`, 'info');
}

/* ---------------------------- render ---------------------------- */
function renderDashboard(d) {
  renderEtlStatus(d);
  renderKpis(d.kpis, d.ocupacao);
  renderSerieChart(d.serie);
  renderStatusChart(d.funil_status);
  renderSaboresChart(d.top_sabores);
  renderOcupacaoChart(d.serie);
  renderTopClientes(d.top_clientes);
  renderTopSabores(d.top_sabores);
  renderQualidade(d.qualidade);
  renderLinhagem(d.etl);
  renderHistorico(d.historico);
  renderFontes(d.fontes);
}

function renderEtlStatus(d) {
  const el = document.getElementById('dashEtlStatus');
  const e = d.etl || {};
  const p = e.periodo || {};
  el.className = 'etl-status ' + (e.status === 'ok' ? 'ok' : 'erro');
  const etapas = (e.etapas || []).map(x => `${x.etapa}:${x.registros}`).join(' -> ');
  el.innerHTML = `
    <span class="tag">${e.status === 'ok' ? 'OK' : 'ERRO'}</span>
    <span>Pipeline v${e.versao_pipeline} | ${p.inicio} a ${p.fim} | ${e.duracao_ms} ms</span>
    <span class="tag">${etapas}</span>
    <span class="tag">${d.qualidade?.total_alertas || 0} alertas de qualidade</span>
    ${e.snapshot_id ? `<span class="tag">snapshot #${e.snapshot_id}</span>` : '<span class="tag">nao persistido</span>'}
  `;
}

function kpiCard(label, valor, sub, classe = '') {
  return `<div class="kpi-card ${classe}">
    <span class="kpi-label">${label}</span>
    <span class="kpi-value">${valor}</span>
    ${sub ? `<span class="kpi-sub">${sub}</span>` : ''}
  </div>`;
}

function renderKpis(k, ocup) {
  const el = document.getElementById('dashKpis');
  el.innerHTML = [
    kpiCard('Faturamento confirmado', moedaBR(k.faturamento_confirmado), `${k.confirmadas} reservas confirmadas`, 'good'),
    kpiCard('Receita potencial', moedaBR(k.receita_potencial), `${k.pendentes} pendentes`, 'warn'),
    kpiCard('Receita perdida', moedaBR(k.receita_perdida), `${k.canceladas} canceladas`, 'bad'),
    kpiCard('Ticket medio', moedaBR(k.ticket_medio), `Lead time medio: ${k.lead_time_medio_dias ?? 'n/d'} dias`),
    kpiCard('Taxa de confirmacao', `${k.taxa_confirmacao}%`, `Cancelamento: ${k.taxa_cancelamento}%`, k.taxa_confirmacao >= 60 ? 'good' : 'warn'),
    kpiCard('Sorvetes vendidos', k.sorvetes_vendidos, `${k.carrinho_dias} carrinho-dias`),
    kpiCard('Clientes unicos', k.clientes_unicos, `Recorrencia: ${k.taxa_recorrencia}%`),
    kpiCard('Ocupacao da frota', `${ocup.taxa_ocupacao}%`, `${ocup.dias_lotados} dias lotados de ${ocup.dias_analisados}`, ocup.taxa_ocupacao > 80 ? 'warn' : 'good'),
  ].join('');
}

function criarChart(id, config) {
  const canvas = document.getElementById(id);
  if (!canvas || typeof Chart === 'undefined') return;
  if (dash.charts[id]) dash.charts[id].destroy();
  dash.charts[id] = new Chart(canvas.getContext('2d'), config);
}

function renderSerieChart(serie) {
  criarChart('chartSerie', {
    type: 'bar',
    data: {
      labels: serie.map(s => s.periodo),
      datasets: [
        { label: 'Faturamento (R$)', data: serie.map(s => s.faturamento), backgroundColor: '#9B7FE8', yAxisID: 'y' },
        { label: 'Confirmadas', data: serie.map(s => s.confirmadas), type: 'line', borderColor: '#7FD9B4', backgroundColor: '#7FD9B4', tension: .3, yAxisID: 'y1' },
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      scales: {
        y: { beginAtZero: true },
        y1: { beginAtZero: true, position: 'right', grid: { drawOnChartArea: false } },
      },
      plugins: { legend: { position: 'bottom' } },
    },
  });
}

function renderStatusChart(funil) {
  criarChart('chartStatus', {
    type: 'doughnut',
    data: {
      labels: funil.map(f => f.label),
      datasets: [{ data: funil.map(f => f.total), backgroundColor: ['#7FD9B4', '#F4C561', '#C0A8C0'], borderWidth: 0 }],
    },
    options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: 'bottom' } } },
  });
}

function renderSaboresChart(sabores) {
  criarChart('chartSabores', {
    type: 'bar',
    data: {
      labels: sabores.map(s => s.sabor),
      datasets: [{ label: 'Receita (R$)', data: sabores.map(s => s.receita), backgroundColor: '#E87FA8' }],
    },
    options: { indexAxis: 'y', responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } } },
  });
}

function renderOcupacaoChart(serie) {
  criarChart('chartOcupacao', {
    type: 'line',
    data: {
      labels: serie.map(s => s.periodo),
      datasets: [{ label: 'Carrinho-dias', data: serie.map(s => s.carrinhos), borderColor: '#9B7FE8', backgroundColor: 'rgba(155,127,232,.22)', fill: true, tension: .35 }],
    },
    options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: 'bottom' } } },
  });
}

function renderTopClientes(clientes) {
  const tb = document.getElementById('dashTopClientes');
  tb.innerHTML = clientes.length
    ? clientes.map(c => `<tr>
        <td><strong>${c.cliente}</strong></td>
        <td>${c.telefone || '-'}</td>
        <td>${c.reservas}</td>
        <td>${moedaBR(c.total)}</td>
        <td>${moedaBR(c.ticket_medio)}</td>
      </tr>`).join('')
    : '<tr><td colspan="5" style="text-align:center;padding:24px;color:var(--mid)">Sem dados</td></tr>';
}

function renderTopSabores(sabores) {
  const tb = document.getElementById('dashTopSabores');
  tb.innerHTML = sabores.length
    ? sabores.map(s => `<tr>
        <td>${s.sabor}</td>
        <td>${s.quantidade}</td>
        <td>${moedaBR(s.receita)}</td>
        <td>${s.participacao}%</td>
      </tr>`).join('')
    : '<tr><td colspan="4" style="text-align:center;padding:24px;color:var(--mid)">Sem dados</td></tr>';
}

function renderQualidade(q) {
  const tb = document.getElementById('dashQualidade');
  const resumo = document.getElementById('dashQualidadeResumo');
  const s = q.por_severidade || { alta: 0, media: 0, baixa: 0 };
  resumo.textContent = `${s.alta} alta | ${s.media} media | ${s.baixa} baixa`;

  tb.innerHTML = (q.alertas || []).length
    ? q.alertas.map(a => `<tr>
        <td><span class="sev ${a.severidade}">${a.severidade}</span></td>
        <td><strong>${a.tipo}</strong></td>
        <td>${a.total}</td>
        <td style="font-size:11px;color:var(--mid)">${(a.exemplos || []).join('<br>') || '-'}</td>
        <td style="font-size:11px">${a.sugestao}</td>
      </tr>`).join('')
    : '<tr><td colspan="5" style="text-align:center;padding:24px;color:var(--mid)">Nenhum problema de qualidade detectado</td></tr>';
}

function renderLinhagem(etl) {
  const el = document.getElementById('dashLinhagem');
  el.innerHTML = (etl.etapas || []).map(e => `
    <div class="log-etapa">
      <span class="etapa-nome">${e.etapa}</span>
      <span>${e.descricao}</span>
      <span class="etapa-meta">${e.fonte} | ${e.registros} reg | ${e.ms} ms</span>
    </div>
  `).join('');
}

function renderHistorico(historico) {
  const tb = document.getElementById('dashHistorico');
  tb.innerHTML = (historico || []).length
    ? historico.map(h => `<tr>
        <td>#${h.id}</td>
        <td>${fmtBR(h.periodo_inicio)} a ${fmtBR(h.periodo_fim)}</td>
        <td>${h.total_registros}</td>
        <td>${moedaBR(h.kpis.faturamento_confirmado)}</td>
        <td>${h.kpis.alertas_qualidade}</td>
        <td style="font-size:11px;color:var(--mid)">${new Date(h.criado_em).toLocaleString('pt-BR')}<br>${h.criado_por}</td>
        <td><button class="btn btn-outline btn-sm" onclick="verSnapshot(${h.id})">Ver</button></td>
      </tr>`).join('')
    : '<tr><td colspan="7" style="text-align:center;padding:24px;color:var(--mid)">Nenhuma execucao gravada ainda</td></tr>';
}

async function verSnapshot(id) {
  try {
    dash.dados = await apiFetch(`/api/etl/snapshot/${id}/`);
    renderDashboard(dash.dados);
    showToast(`Snapshot #${id} carregado`, 'info');
  } catch (e) {
    showToast('Erro ao carregar snapshot: ' + e.message, 'error');
  }
}

function renderFontes(fontes) {
  const tb = document.getElementById('dashFontes');
  tb.innerHTML = (fontes || []).map(f => `<tr>
    <td><code>${f.tabela}</code></td>
    <td><span class="sev ${f.papel === 'fato' ? 'alta' : 'baixa'}">${f.papel}</span></td>
    <td>${f.descricao}</td>
    <td style="font-size:11px">${f.campos_chave.join(', ')}</td>
    <td style="font-size:11px">${f.campos_sensiveis.join(', ') || '-'}</td>
  </tr>`).join('');
}

/* -------------------------- EXECUTOR ---------------------------- */
function runPreencherPadrao() {
  const hoje = new Date();
  const i = document.getElementById('runInicio');
  const f = document.getElementById('runFim');
  if (i && !i.value) i.value = `${hoje.getFullYear()}-01-01`;
  if (f && !f.value) f.value = `${hoje.getFullYear()}-12-31`;
  const c = document.getElementById('runCsvPath');
  if (c && !c.value) c.value = 'relatorios/etl.csv';
  const p = document.getElementById('runPdfPath');
  if (p && !p.value) p.value = 'relatorios/etl.pdf';
  runComandoPreview();
}

function runParams() {
  const val = id => document.getElementById(id)?.value ?? '';
  const chk = id => !!document.getElementById(id)?.checked;
  return {
    inicio: val('runInicio'),
    fim: val('runFim'),
    granularidade: val('runGranularidade') || 'mes',
    status: val('runStatus') || null,
    persistir: chk('runPersistir'),
    exportar_csv: chk('runCsv') ? (val('runCsvPath') || 'relatorios/etl.csv') : null,
    exportar_pdf: chk('runPdf') ? (val('runPdfPath') || 'relatorios/etl.pdf') : null,
  };
}

function runComandoPreview() {
  const p = runParams();
  let cmd = `python manage.py rodar_etl --inicio ${p.inicio || 'AAAA-MM-DD'} --fim ${p.fim || 'AAAA-MM-DD'} --granularidade ${p.granularidade}`;
  if (p.status) cmd += ` --status ${p.status}`;
  if (!p.persistir) cmd += ' --sem-persistir';
  if (p.exportar_csv) cmd += ` --exportar-csv ${p.exportar_csv}`;
  if (p.exportar_pdf) cmd += ` --exportar-pdf ${p.exportar_pdf}`;
  const el = document.getElementById('runComando');
  if (el) el.textContent = cmd;
}

function runPreset(nome) {
  const hoje = new Date();
  const iso = d => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  const set = (id, v) => { const el = document.getElementById(id); if (el) el.value = v; };
  const chk = (id, v) => { const el = document.getElementById(id); if (el) el.checked = v; };

  if (nome === 'mes') {
    set('runInicio', `${hoje.getFullYear()}-${String(hoje.getMonth() + 1).padStart(2, '0')}-01`);
    set('runFim', iso(new Date(hoje.getFullYear(), hoje.getMonth() + 1, 0)));
    chk('runPersistir', true); chk('runCsv', true); chk('runPdf', false);
  } else if (nome === 'ano') {
    set('runInicio', `${hoje.getFullYear()}-01-01`);
    set('runFim', `${hoje.getFullYear()}-12-31`);
    chk('runPersistir', true); chk('runCsv', true); chk('runPdf', true);
  } else if (nome === '90') {
    const ini = new Date(hoje); ini.setDate(ini.getDate() - 90);
    set('runInicio', iso(ini)); set('runFim', iso(hoje));
    chk('runPersistir', true); chk('runCsv', true); chk('runPdf', false);
  } else if (nome === 'consulta') {
    chk('runPersistir', false); chk('runCsv', false); chk('runPdf', false);
  }
  runComandoPreview();
  showToast('Atalho aplicado. Revise e clique em Executar ETL.', 'info');
}

function runLimparConsole() {
  const el = document.getElementById('runConsole');
  if (el) el.textContent = 'Pronto. Clique em "Executar ETL" para rodar o pipeline.';
  const badge = document.getElementById('runStatusBadge');
  if (badge) badge.textContent = '';
}

function renderRunArquivos(arquivos) {
  const el = document.getElementById('runArquivos');
  if (!arquivos || !arquivos.length) {
    el.innerHTML = '<p class="run-vazio">Nenhum arquivo gerado nesta execucao.</p>';
    return;
  }
  el.innerHTML = arquivos.map(a => `
    <div class="run-arquivo">
      <span class="run-arquivo-nome">${a.nome}</span>
      <span>${(a.bytes / 1024).toFixed(1)} KB</span>
      <span class="run-arquivo-meta">${a.relativo}</span>
      <a class="btn btn-primary btn-sm" href="${a.url}" download>Baixar</a>
    </div>
  `).join('');
}

async function runAtualizarHistorico() {
  const tb = document.getElementById('runHistorico');
  if (!tb) return;
  try {
    const lista = await apiFetch('/api/etl/execucoes/');
    tb.innerHTML = lista.length
      ? lista.map(e => `<tr>
          <td>#${e.id}</td>
          <td style="font-size:11px;color:var(--mid)">${new Date(e.criado_em).toLocaleString('pt-BR')}</td>
          <td>${e.criado_por}</td>
          <td style="font-size:11px"><code>${e.comando}</code></td>
          <td><span class="sev ${e.status === 'ok' ? 'baixa' : 'alta'}">${e.status}</span></td>
          <td>${e.duracao_ms} ms</td>
          <td>${(e.arquivos || []).map(a => `<a href="${a.url}" download>${a.nome}</a>`).join(', ') || '-'}</td>
        </tr>`).join('')
      : '<tr><td colspan="7" style="text-align:center;padding:24px;color:var(--mid)">Nenhuma execucao registrada</td></tr>';
  } catch (e) {
    tb.innerHTML = `<tr><td colspan="7" style="text-align:center;padding:24px;color:var(--red)">${e.message}</td></tr>`;
  }
}

async function runExecutar() {
  if (run.executando) return;
  const p = runParams();
  if (!p.inicio || !p.fim) { showToast('Informe o periodo (data inicial e final).', 'error'); return; }

  run.executando = true;
  const btn = document.getElementById('runBtn');
  const consoleEl = document.getElementById('runConsole');
  const badge = document.getElementById('runStatusBadge');
  if (btn) btn.disabled = true;
  badge.textContent = 'executando';
  consoleEl.textContent = `$ ${document.getElementById('runComando').textContent}\n\nExecutando...`;

  try {
    const r = await apiFetch('/api/etl/executar-comando/', { method: 'POST', body: JSON.stringify(p) });
    consoleEl.textContent = `$ ${r.comando}\n\n${r.saida}`;
    badge.textContent = r.status === 'ok' ? `ok | ${r.duracao_ms} ms` : 'erro';
    renderRunArquivos(r.arquivos);
    runAtualizarHistorico();
    dash.dados = null;
    showToast(r.status === 'ok' ? 'ETL executado com sucesso.' : 'ETL terminou com erro.', r.status === 'ok' ? 'success' : 'error');
  } catch (e) {
    consoleEl.textContent += `\n\n[erro] ${e.message}`;
    badge.textContent = 'erro';
    showToast('Erro ao executar: ' + e.message, 'error');
  } finally {
    run.executando = false;
    if (btn) btn.disabled = false;
  }
}

/* ------------------- integracao com o painel -------------------- */
function etlAoAbrirView(view) {
  if (view === 'dashboard') {
    dashPreencherFiltros();
    if (!dash.dados) carregarDashboard();
  }
  if (view === 'executar') {
    runPreencherPadrao();
    runAtualizarHistorico();
  }
}

if (typeof viewTitles !== 'undefined') {
  Object.assign(viewTitles, {
    dashboard: 'Dashboard ETL',
    executar: 'Executar ETL',
  });
}

(function envolverSetView() {
  const original = window.setView;
  if (typeof original !== 'function') return;
  window.setView = function (view, btn) {
    original(view, btn);
    etlAoAbrirView(view);
  };
})();
'''


# ═══════════════════════════════════════════════════════════════════
#  APLICACAO
# ═══════════════════════════════════════════════════════════════════
def criar_pacote_etl(app: Path):
    gravar_novo(app / "etl" / "__init__.py", ETL_INIT, "pacote etl")
    gravar_novo(app / "etl" / "extract.py", ETL_EXTRACT, "extract.py")
    gravar_novo(app / "etl" / "transform.py", ETL_TRANSFORM, "transform.py")
    gravar_novo(app / "etl" / "load.py", ETL_LOAD, "load.py")
    gravar_novo(app / "etl" / "exporters.py", ETL_EXPORTERS, "exporters.py")
    gravar_novo(app / "etl" / "pipeline.py", ETL_PIPELINE, "pipeline.py")

    gravar_novo(app / "api_etl.py", API_ETL, "api_etl.py")

    gravar_novo(app / "management" / "__init__.py", "", "management/__init__.py")
    gravar_novo(app / "management" / "commands" / "__init__.py", CMD_INIT,
                "management/commands/__init__.py")
    gravar_novo(app / "management" / "commands" / "rodar_etl.py", CMD_RODAR_ETL,
                "management/commands/rodar_etl.py")


def patch_models(app: Path):
    p = Patch(app / "models.py", "aluguel/models.py")
    p.garantir_linha("from django.conf import settings", "from django.db import models",
                     "import settings")
    p.garantir_linha("    criado_em = models.DateTimeField(auto_now_add=True, null=True)  # ETL",
                     "class Reserva(models.Model):", "campo Reserva.criado_em")
    p.bloco(MODELS_BLOCO, "# --- ETL:MODELOS:INICIO ---", "# --- ETL:MODELOS:FIM ---",
            "modelos RelatorioETL/ExecucaoETL", anexar=True)
    p.finalizar()


def patch_urls(app: Path):
    p = Patch(app / "urls.py", "aluguel/urls.py")
    p.garantir_linha("from . import api_etl", "from . import api", "import api_etl")
    p.bloco(URLS_ROTAS, "# --- ETL:ROTAS:INICIO ---", "# --- ETL:ROTAS:FIM ---",
            "rotas /api/etl/*",
            ancora="    path('api/carrinhos/', api.api_carrinhos, name='api_carrinhos'),",
            depois=True,
            ancoras_alt=["urlpatterns = ["])
    p.finalizar()


def patch_settings(raiz: Path):
    p = Patch(raiz / "meusite" / "settings.py", "meusite/settings.py")
    if not p.existe:
        registrar("meusite/settings.py", "config ETL", "ARQUIVO AUSENTE")
        avisos.append("meusite/settings.py nao encontrado — adicione o bloco ETL manualmente.")
        return
    p.bloco(SETTINGS_BLOCO, "# --- ETL:CONFIG:INICIO ---", "# --- ETL:CONFIG:FIM ---",
            "configuracao ETL", anexar=True)
    p.finalizar()


def patch_painel_html(raiz: Path):
    caminho = raiz / "aluguel" / "templates" / "aluguel" / "painel.html"
    p = Patch(caminho, "painel.html")
    p.bloco(HTML_CHARTJS, "<!-- ETL:CHARTJS:INICIO -->", "<!-- ETL:CHARTJS:FIM -->",
            "CDN Chart.js", ancora="</head>", depois=False,
            ancoras_alt=["{% static 'aluguel/css/painel.css' %}"])
    p.bloco(HTML_NAV, "<!-- ETL:NAV:INICIO -->", "<!-- ETL:NAV:FIM -->",
            "itens de menu", ancora="<hr style=\"border:none;border-top:1px solid",
            depois=False, ancoras_alt=["Ver Site"])
    p.bloco(HTML_VIEWS, "<!-- ETL:VIEWS:INICIO -->", "<!-- ETL:VIEWS:FIM -->",
            "telas Dashboard/Executar",
            ancora="</div><!-- /content -->", depois=False,
            ancoras_alt=["<div class=\"overlay\" id=\"modalReserva\">", "<div class=\"toast\""])
    p.finalizar()


def patch_painel_js(raiz: Path):
    p = Patch(raiz / "aluguel" / "static" / "aluguel" / "js" / "painel.js", "painel.js")
    p.bloco(JS_BLOCO, "// --- ETL:INICIO ---", "// --- ETL:FIM ---",
            "logica do dashboard e executor", anexar=True)
    p.finalizar()


def patch_painel_css(raiz: Path):
    p = Patch(raiz / "aluguel" / "static" / "aluguel" / "css" / "painel.css", "painel.css")
    p.bloco(CSS_BLOCO, "/* --- ETL:INICIO --- */", "/* --- ETL:FIM --- */",
            "estilos do dashboard e executor", anexar=True)
    p.finalizar()


def patch_requirements(raiz: Path):
    caminho = raiz / "requirements.txt"
    if not caminho.exists():
        gravar_novo(caminho, "Django>=6.0.3\nreportlab>=4.2.0\n", "reportlab")
        return
    texto = ler(caminho)
    if "reportlab" in texto:
        registrar("requirements.txt", "reportlab", "já aplicado")
        return
    backup(caminho)
    if not DRY:
        caminho.write_text(texto.rstrip() + "\nreportlab>=4.2.0\n", encoding="utf-8")
    registrar("requirements.txt", "reportlab", "aplicado")


def patch_gitignore(raiz: Path):
    caminho = raiz / ".gitignore"
    if not caminho.exists():
        return
    texto = ler(caminho)
    linhas = []
    if "relatorios/" not in texto:
        linhas.append("relatorios/")
    if "*.bak_" not in texto:
        linhas.append("*.bak_*")
    if not linhas:
        registrar(".gitignore", "relatorios/ e *.bak_*", "já aplicado")
        return
    backup(caminho)
    if not DRY:
        caminho.write_text(texto.rstrip() + "\n\n# ETL\n" + "\n".join(linhas) + "\n",
                           encoding="utf-8")
    registrar(".gitignore", ", ".join(linhas), "aplicado")


# ────────────────────── migracao automatica ───────────────────────
def ultima_migracao(app: Path) -> str:
    migracoes = sorted((app / "migrations").glob("[0-9]*_*.py"))
    for m in reversed(migracoes):
        if "etl_relatorio_execucao" not in m.name:
            return m.stem
    return "0001_initial"


def proximo_numero(app: Path) -> str:
    numeros = [int(m.name.split("_")[0]) for m in (app / "migrations").glob("[0-9]*_*.py")
               if m.name.split("_")[0].isdigit()]
    return f"{(max(numeros) + 1) if numeros else 1:04d}"


def gerar_migracao(app: Path) -> Path | None:
    existentes = list((app / "migrations").glob("*_etl_relatorio_execucao.py"))
    if existentes:
        registrar(rel(existentes[0]), "migracao ETL", "já aplicado")
        return existentes[0]

    dependencia = ultima_migracao(app)
    numero = proximo_numero(app)
    caminho = app / "migrations" / f"{numero}_etl_relatorio_execucao.py"

    conteudo = f'''# -*- coding: utf-8 -*-
# ETL:GERADO por instalar_etl.py — nao editar manualmente
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("aluguel", "{dependencia}"),
    ]

    operations = [
        migrations.AddField(
            model_name="reserva",
            name="criado_em",
            field=models.DateTimeField(auto_now_add=True, null=True),
        ),
        migrations.CreateModel(
            name="RelatorioETL",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name="ID")),
                ("periodo_inicio", models.DateField()),
                ("periodo_fim", models.DateField()),
                ("granularidade", models.CharField(
                    choices=[("dia", "Diario"), ("semana", "Semanal"), ("mes", "Mensal")],
                    default="mes", max_length=10)),
                ("total_registros", models.PositiveIntegerField(default=0)),
                ("duracao_ms", models.PositiveIntegerField(default=0)),
                ("payload", models.JSONField(default=dict)),
                ("log", models.JSONField(blank=True, default=list)),
                ("criado_em", models.DateTimeField(auto_now_add=True)),
                ("criado_por", models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    to=settings.AUTH_USER_MODEL)),
            ],
            options={{
                "verbose_name": "Relatorio ETL",
                "verbose_name_plural": "Relatorios ETL",
                "ordering": ["-criado_em"],
            }},
        ),
        migrations.CreateModel(
            name="ExecucaoETL",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name="ID")),
                ("comando", models.TextField()),
                ("parametros", models.JSONField(blank=True, default=dict)),
                ("status", models.CharField(
                    choices=[("rodando", "Rodando"), ("ok", "Sucesso"), ("erro", "Erro")],
                    default="rodando", max_length=10)),
                ("saida", models.TextField(blank=True, default="")),
                ("duracao_ms", models.PositiveIntegerField(default=0)),
                ("arquivos", models.JSONField(blank=True, default=list)),
                ("criado_em", models.DateTimeField(auto_now_add=True)),
                ("criado_por", models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    to=settings.AUTH_USER_MODEL)),
            ],
            options={{
                "verbose_name": "Execucao ETL",
                "verbose_name_plural": "Execucoes ETL",
                "ordering": ["-criado_em"],
            }},
        ),
    ]
'''
    gravar_novo(caminho, conteudo, "migracao ETL")
    return caminho


# ───────────────────── checagens de ambiente ──────────────────────
def tem_reportlab() -> bool:
    try:
        subprocess.run([sys.executable, "-c", "import reportlab"],
                       check=True, capture_output=True)
        return True
    except Exception:
        return False


def rodar_manage(*args) -> tuple[int, str]:
    try:
        r = subprocess.run([sys.executable, "manage.py", *args], cwd=RAIZ,
                           capture_output=True, text=True, timeout=300)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except Exception as exc:
        return 1, str(exc)


def checar_ambiente(app: Path):
    if not tem_reportlab():
        if INSTALAR_DEPS and not DRY:
            print("\n[..] Instalando reportlab...")
            r = subprocess.run([sys.executable, "-m", "pip", "install", "reportlab"],
                               capture_output=True, text=True)
            if r.returncode == 0:
                print("     reportlab instalado.")
            else:
                avisos.append("Falha ao instalar reportlab. Rode: pip install reportlab")
        else:
            avisos.append("reportlab NAO instalado — o botao PDF vai falhar. "
                          "Rode: pip install reportlab (ou use --instalar-deps).")

    # duplicata conhecida em main.js
    main_js = RAIZ / "aluguel" / "static" / "aluguel" / "js" / "main.js"
    if main_js.exists() and ler(main_js).count("async function enviarFormularioServidor(") > 1:
        avisos.append("main.js: 'enviarFormularioServidor' esta definida mais de uma vez "
                      "(a 2a sobrescreve a 1a). Remova a duplicata manualmente.")

    # rotas que o painel.js usa e que ainda podem nao existir
    urls = RAIZ / "aluguel" / "urls.py"
    if urls.exists():
        texto = ler(urls)
        if "api/bloqueios/" not in texto:
            avisos.append("aluguel/urls.py: rota 'api/bloqueios/' nao existe, mas painel.js "
                          "usa. O calendario do admin seguira caindo no fallback.")
        if "api/carrinhos/<int" not in texto:
            avisos.append("aluguel/urls.py: rota 'api/carrinhos/<id>/' (POST) nao existe; "
                          "salvar preco/status do carrinho nao persiste.")


# ────────────────────────────── main ──────────────────────────────
def main():
    global RAIZ

    base = Path(ARGS[0]).resolve() if ARGS else Path(__file__).resolve().parent
    raiz = encontrar_raiz(base)
    if not raiz:
        print(f"[ERRO] Nao encontrei manage.py a partir de: {base}")
        sys.exit(1)
    RAIZ = raiz

    app = raiz / "aluguel"
    if not app.exists():
        print(f"[ERRO] Pasta do app nao encontrada: {app}")
        sys.exit(1)

    print("=" * 70)
    print("INSTALADOR ETL + DASHBOARD + EXECUTOR GRAFICO")
    print("=" * 70)
    print(f"Projeto : {raiz}")
    print(f"Modo    : {'DRY-RUN (nada e gravado)' if DRY else 'APLICANDO'}")
    print(f"Backups : {'desativados' if SEM_BACKUP else 'ativos (.bak_' + STAMP + ')'}")
    print(f"Validar : {'nao' if SEM_VALIDAR else 'sim (ast.parse nos .py)'}")
    print()

    print("[1/6] Criando pacote ETL e comandos...")
    criar_pacote_etl(app)

    print("[2/6] Ajustando models.py, urls.py e settings.py...")
    patch_models(app)
    patch_urls(app)
    patch_settings(raiz)

    print("[3/6] Injetando telas no painel (HTML/JS/CSS)...")
    patch_painel_html(raiz)
    patch_painel_js(raiz)
    patch_painel_css(raiz)

    print("[4/6] Ajustando requirements.txt e .gitignore...")
    patch_requirements(raiz)
    patch_gitignore(raiz)

    print("[5/6] Gerando migracao...")
    migracao = gerar_migracao(app)

    print("[6/6] Checando ambiente...")
    checar_ambiente(app)

    # ── migrate ──
    if not DRY and not SEM_MIGRATE:
        print("\n[..] Rodando 'python manage.py migrate'...")
        codigo, saida = rodar_manage("migrate")
        if codigo == 0:
            print("     migrate OK.")
            resultados.append(("manage.py migrate", "aplicar migracoes", "aplicado"))
        else:
            resultados.append(("manage.py migrate", "aplicar migracoes", "NÃO ENCONTRADO"))
            avisos.append("'manage.py migrate' falhou — rode manualmente e leia a saida:")
            avisos.append("   " + "\n   ".join(saida.strip().splitlines()[-6:]))
    elif DRY:
        print("\n[--] migrate nao executado (dry-run).")

    # ── drift de migracao ──
    if not DRY:
        codigo, _ = rodar_manage("makemigrations", "aluguel", "--check", "--dry-run")
        if codigo != 0:
            avisos.append("Existem mudancas de modelo nao migradas. Rode: "
                          "python manage.py makemigrations aluguel && python manage.py migrate")

    # ── relatorio ──
    print("\n" + "=" * 70)
    print("RESULTADO")
    print("=" * 70)
    if not resultados:
        print("  (nada a fazer)")
    for arquivo, item, status in resultados:
        print(f"  {MARCA.get(status, '[ ?  ]')} {arquivo}: {item} ({status})")

    print("\n" + "=" * 70)
    print("INTERVENCAO MANUAL")
    print("=" * 70)
    if avisos:
        for a in dict.fromkeys(avisos):
            print("  -", a)
    else:
        print("  Nenhuma pendencia detectada.")

    print("\n" + "=" * 70)
    print("PASSOS OBRIGATORIOS / COMO USAR")
    print("=" * 70)
    print("  1. pip install -r requirements.txt        (garante o reportlab para o PDF)")
    if DRY or SEM_MIGRATE:
        print("  2. python manage.py migrate")
    print("  3. python manage.py runserver")
    print("  4. Abra /painel/ e faca login como usuario staff")
    print("  5. Menu lateral -> 'Executar ETL' para rodar pelo navegador")
    print("     Menu lateral -> 'Dashboard ETL' para ver KPIs, graficos e exportar")
    print()
    print("  CLI equivalente (para cron / Task Scheduler):")
    print("     python manage.py rodar_etl --inicio 2026-01-01 --fim 2026-12-31 --granularidade mes")
    print("     python manage.py rodar_etl --exportar-csv relatorios/etl.csv "
          "--exportar-pdf relatorios/etl.pdf")
    print()
    print("  Endpoints criados (todos exigem login staff):")
    print("     GET  /api/etl/dashboard/         -> payload analitico")
    print("     POST /api/etl/executar/          -> roda E-T-L e grava snapshot")
    print("     POST /api/etl/executar-comando/  -> executa o rodar_etl (usado pela tela)")
    print("     GET  /api/etl/export/csv/        -> download CSV (?tipo=linhas|serie|...)")
    print("     GET  /api/etl/export/pdf/        -> download PDF")
    print("     GET  /api/etl/download/          -> baixa arquivo gerado")
    print("     GET  /api/etl/historico/         -> snapshots")
    print("     GET  /api/etl/execucoes/         -> historico de execucoes")
    print()
    if not DRY:
        print(f"  Backups: cada arquivo alterado virou '<nome>.bak_{STAMP}'")
        print("  Para desfazer tudo: restaure os .bak e apague aluguel/etl, api_etl.py,")
        print("  management/commands/rodar_etl.py e a migracao gerada.")
    else:
        print("  >> Nada foi gravado. Rode sem --dry para aplicar.")
    print("=" * 70)


if __name__ == "__main__":
    main()