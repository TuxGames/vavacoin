"""O imposto do cassino sobre o lucro tirado dos cidadãos de cada reino.

Três coisas que estes testes existem para guardar:

1. **A atribuição congela na aposta.** Se ela saísse da cidadania atual,
   alguém entrando ou saindo do reino reescreveria imposto de rodada passada,
   e a conta do mês mudaria sozinha.
2. **Prejuízo vira abatimento, não imposto negativo** — e o abatimento reduz o
   lucro tributável, não o imposto.
3. **Liquidar é uma vez só**, e o abatimento é consumido junto com o
   pagamento, sob a mesma guarda.
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from conftest import conservacao

from vavacoin.auditoria import auditar
from vavacoin.caladinho import (
    criar_casa,
    criar_rodada,
    definir_dono,
    retirar,
    revelar_casa,
)
from vavacoin.dinheiro import ZERO
from vavacoin.erros import SemAutoridade, ValorInvalido
from vavacoin.extensoes import db
from vavacoin.limite import limpar_tudo
from vavacoin.moeda import soma_saldos, supply_emitido
from vavacoin.modelos import (
    LiquidacaoDeImposto,
    RodadaMines,
    Transacao,
    Usuario,
    agora,
)
from vavacoin.operacoes import ajustar_saldo
from vavacoin.reinos import criar_reino, definir_operador, entrar_no_reino, sair_do_reino
from vavacoin.tributo import (
    ALIQUOTA_MAXIMA,
    ALIQUOTA_MINIMA,
    TIPO_IMPOSTO_CASSINO,
    definir_aliquota,
    liquidar,
    lucro_do_periodo,
    panorama,
    previsao,
)

SENHA = "senha-boa-123"


@pytest.fixture(autouse=True)
def limite_limpo():
    limpar_tudo()
    yield
    limpar_tudo()


@pytest.fixture
def cena(app, bc, nova_pessoa):
    """Cassino com dono, um reino, um cidadão e um forasteiro."""
    casa = criar_casa(autoridade=bc)
    db.session.commit()
    ajustar_saldo(casa, "4000.00", "caixa", autoridade=bc)
    db.session.commit()

    gustavo = nova_pessoa(nome="gustavo", saldo="50.00")
    definir_dono(gustavo, autoridade=bc)
    db.session.commit()

    reino = criar_reino("Alfheim", autoridade=bc)
    db.session.commit()
    rei = nova_pessoa(nome="rei", saldo="20.00")
    definir_operador(reino, rei, autoridade=bc)
    db.session.commit()

    cidadao = nova_pessoa(nome="ana", saldo="200.00")
    entrar_no_reino(reino, cidadao)
    db.session.commit()
    forasteiro = nova_pessoa(nome="zeca", saldo="200.00")
    db.session.commit()

    return {
        "casa": casa,
        "dono": gustavo,
        "reino": reino,
        "rei": rei,
        "cidadao": cidadao,
        "forasteiro": forasteiro,
    }


def _janela():
    """Um período que cobre agora, com folga dos dois lados."""
    return agora() - timedelta(days=1), agora() + timedelta(days=1)


def _perder(jogador, aposta="10.00"):
    """Uma rodada que o jogador perde: a casa fica com a aposta inteira."""
    rodada = criar_rodada(jogador, aposta, minas_escolhidas=24)
    db.session.commit()
    mina = rodada.casas_com_mina[0]
    revelar_casa(jogador, mina)
    db.session.commit()
    return rodada


def _ganhar(jogador, aposta="10.00"):
    """Uma rodada em que o jogador abre uma casa segura e saca."""
    rodada = criar_rodada(jogador, aposta, minas_escolhidas=1)
    db.session.commit()
    segura = next(c for c in range(25) if c not in rodada.casas_com_mina)
    revelar_casa(jogador, segura)
    db.session.commit()
    retirar(jogador)
    db.session.commit()
    return rodada


def _auditoria_fecha():
    relatorio = auditar()
    assert relatorio["ok"], relatorio
    assert relatorio["ledger"]["saldos_divergentes"] == []
    return True


# --- a atribuição congela na aposta -----------------------------------------


def test_a_rodada_guarda_o_reino_do_jogador(app, bc, cena):
    rodada = _perder(cena["cidadao"])

    assert rodada.reino_id == cena["reino"].id


def test_quem_nao_e_de_reino_nenhum_fica_com_nulo(app, bc, cena):
    """Nulo é o "não cidadão", e é valor legítimo — não dado faltando."""
    rodada = _perder(cena["forasteiro"])

    assert rodada.reino_id is None


def test_sair_do_reino_nao_reescreve_rodada_passada(app, bc, cena):
    """O teste que a feature inteira existe para garantir.

    Se a atribuição fosse calculada depois, sair do reino tiraria o lucro
    passado da conta dele e o imposto do mês mudaria sozinho.
    """
    reino, cidadao = cena["reino"], cena["cidadao"]
    inicio, fim = _janela()
    _perder(cidadao, "10.00")
    assert lucro_do_periodo(reino.id, inicio, fim) == Decimal("10.00")

    sair_do_reino(reino, cidadao)
    db.session.commit()
    db.session.expire_all()

    assert lucro_do_periodo(reino.id, inicio, fim) == Decimal("10.00")


def test_entrar_no_reino_nao_puxa_rodada_antiga(app, bc, cena):
    """O outro lado: entrar hoje não torna o cassino devedor de ontem."""
    reino, forasteiro = cena["reino"], cena["forasteiro"]
    inicio, fim = _janela()
    _perder(forasteiro, "10.00")
    assert lucro_do_periodo(reino.id, inicio, fim) == ZERO

    entrar_no_reino(reino, forasteiro)
    db.session.commit()
    db.session.expire_all()

    assert lucro_do_periodo(reino.id, inicio, fim) == ZERO
    assert lucro_do_periodo(None, inicio, fim) == Decimal("10.00")


# --- o lucro por reino ------------------------------------------------------


def test_o_lucro_separa_cidadao_de_forasteiro(app, bc, cena):
    reino = cena["reino"]
    inicio, fim = _janela()

    _perder(cena["cidadao"], "10.00")
    _perder(cena["forasteiro"], "7.00")

    assert lucro_do_periodo(reino.id, inicio, fim) == Decimal("10.00")
    assert lucro_do_periodo(None, inicio, fim) == Decimal("7.00")


def test_o_lucro_desconta_o_que_a_casa_pagou(app, bc, cena):
    """Apostas menos prêmios: o que a casa ganhou de fato."""
    reino, cidadao = cena["reino"], cena["cidadao"]
    inicio, fim = _janela()

    _perder(cidadao, "10.00")
    ganha = _ganhar(cidadao, "10.00")

    esperado = Decimal("10.00") + (ganha.aposta - ganha.premio)
    assert lucro_do_periodo(reino.id, inicio, fim) == esperado


def test_rodada_fora_do_periodo_nao_conta(app, bc, cena):
    reino, cidadao = cena["reino"], cena["cidadao"]
    _perder(cidadao, "10.00")

    antigo_inicio = agora() - timedelta(days=10)
    antigo_fim = agora() - timedelta(days=5)
    assert lucro_do_periodo(reino.id, antigo_inicio, antigo_fim) == ZERO


def test_rodada_aberta_nao_conta(app, bc, cena):
    """Tributar rodada aberta seria tributar dinheiro que talvez volte."""
    reino, cidadao = cena["reino"], cena["cidadao"]
    inicio, fim = _janela()
    criar_rodada(cidadao, "10.00", minas_escolhidas=3)
    db.session.commit()

    assert lucro_do_periodo(reino.id, inicio, fim) == ZERO


# --- a alíquota -------------------------------------------------------------


def test_a_aliquota_nasce_em_dez_e_e_configuravel(app, bc, cena):
    reino, rei = cena["reino"], cena["rei"]
    assert reino.aliquota_cassino == Decimal("10.00")

    definir_aliquota(reino, "15.00", rei)
    db.session.commit()

    assert reino.aliquota_cassino == Decimal("15.00")


@pytest.mark.parametrize("ruim", ["-0.01", "50.01", "100", "abc"])
def test_aliquota_fora_da_faixa_e_recusada(app, bc, cena, ruim):
    with pytest.raises(ValorInvalido):
        definir_aliquota(cena["reino"], ruim, cena["rei"])


def test_mudanca_de_aliquota_fica_registrada(app, bc, cena):
    from vavacoin.modelos import RegistroAdministrativo

    definir_aliquota(cena["reino"], "20.00", cena["rei"])
    db.session.commit()

    registro = db.session.execute(
        db.select(RegistroAdministrativo).order_by(RegistroAdministrativo.id.desc())
    ).scalars().first()
    assert "10.00" in registro.detalhe and "20.00" in registro.detalhe


def test_so_o_operador_muda_a_aliquota(app, bc, cena):
    with pytest.raises(SemAutoridade):
        definir_aliquota(cena["reino"], "20.00", cena["cidadao"])


# --- liquidar ---------------------------------------------------------------


def test_liquidar_paga_o_imposto_do_caixa_para_o_cofre(app, bc, cena):
    reino, dono = cena["reino"], cena["dono"]
    inicio, fim = _janela()
    _perder(cena["cidadao"], "75.00")
    antes = conservacao()
    caixa, cofre = cena["casa"].saldo, reino.cofre.saldo

    linha = liquidar(reino, inicio, fim, dono)
    db.session.commit()
    db.session.expire_all()

    # 10% de 75 = 7,50
    assert linha.lucro_bruto == Decimal("75.00")
    assert linha.imposto == Decimal("7.50")
    assert db.session.get(Usuario, cena["casa"].id).saldo == caixa - Decimal("7.50")
    assert db.session.get(Usuario, reino.cofre_id).saldo == cofre + Decimal("7.50")
    assert conservacao() == antes
    assert _auditoria_fecha()


def test_o_imposto_e_um_lancamento_com_tipo_proprio(app, bc, cena):
    reino, dono = cena["reino"], cena["dono"]
    inicio, fim = _janela()
    _perder(cena["cidadao"], "75.00")

    liquidar(reino, inicio, fim, dono)
    db.session.commit()

    lancamento = db.session.execute(
        db.select(Transacao).where(Transacao.tipo == TIPO_IMPOSTO_CASSINO)
    ).scalar_one()
    assert lancamento.origem_id == cena["casa"].id
    assert lancamento.destino_id == reino.cofre_id
    assert lancamento.ator_id == dono.id


def test_o_forasteiro_nao_entra_no_imposto(app, bc, cena):
    """O acordo é sobre o lucro dos cidadãos daquele reino, e só."""
    reino, dono = cena["reino"], cena["dono"]
    inicio, fim = _janela()
    _perder(cena["forasteiro"], "75.00")

    linha = liquidar(reino, inicio, fim, dono)
    db.session.commit()

    assert linha.lucro_bruto == ZERO
    assert linha.imposto == ZERO


def test_so_o_dono_do_cassino_liquida(app, bc, cena):
    """É o caixa dele que paga; o reino recebe, não cobra à força."""
    inicio, fim = _janela()
    _perder(cena["cidadao"], "75.00")

    with pytest.raises(SemAutoridade):
        liquidar(cena["reino"], inicio, fim, cena["rei"])


def test_liquidar_duas_vezes_nao_paga_duas_vezes(app, bc, cena):
    """A guarda de status: (reino, início, fim) é UNIQUE."""
    reino, dono = cena["reino"], cena["dono"]
    inicio, fim = _janela()
    _perder(cena["cidadao"], "75.00")
    antes = conservacao()

    liquidar(reino, inicio, fim, dono)
    db.session.commit()
    caixa = db.session.get(Usuario, cena["casa"].id).saldo

    with pytest.raises(ValorInvalido):
        liquidar(reino, inicio, fim, dono)
    db.session.rollback()

    db.session.expire_all()
    assert db.session.get(Usuario, cena["casa"].id).saldo == caixa
    assert (
        db.session.query(Transacao).filter_by(tipo=TIPO_IMPOSTO_CASSINO).count() == 1
    )
    assert conservacao() == antes


def test_periodo_sem_lucro_nao_move_dinheiro(app, bc, cena):
    reino, dono = cena["reino"], cena["dono"]
    inicio, fim = _janela()
    antes = conservacao()

    linha = liquidar(reino, inicio, fim, dono)
    db.session.commit()

    assert linha.imposto == ZERO
    assert linha.transacao_id is None
    assert conservacao() == antes


# --- prejuízo vira abatimento -----------------------------------------------


def test_prejuizo_nao_gera_imposto_negativo_e_vira_saldo(app, bc, cena):
    """O reino não devolve dinheiro; o prejuízo fica guardado para abater."""
    reino, dono, cidadao = cena["reino"], cena["dono"], cena["cidadao"]
    inicio, fim = _janela()
    antes = conservacao()

    # O cidadão ganha: para a casa, é prejuízo.
    ganha = _ganhar(cidadao, "75.00")
    prejuizo = ganha.premio - ganha.aposta
    assert prejuizo > ZERO

    linha = liquidar(reino, inicio, fim, dono)
    db.session.commit()
    db.session.expire_all()

    assert linha.lucro_bruto == -prejuizo
    assert linha.imposto == ZERO
    assert db.session.get(type(reino), reino.id).abatimento == prejuizo
    assert conservacao() == antes


def test_o_ciclo_inteiro_prejuizo_depois_lucro(app, bc, cena):
    """Período ruim gera saldo; o seguinte paga sobre lucro menos saldo.

    E o abatimento reduz o **lucro tributável**, não o imposto: abater 30 com
    alíquota de 10% tira 3 do imposto, não 30.
    """
    reino, dono, cidadao = cena["reino"], cena["dono"], cena["cidadao"]
    antes = conservacao()

    # --- período 1: a casa perde ---
    p1_inicio = agora() - timedelta(days=2)
    p1_fim = agora() + timedelta(hours=1)
    ganha = _ganhar(cidadao, "75.00")
    prejuizo = ganha.premio - ganha.aposta

    liquidar(reino, p1_inicio, p1_fim, dono)
    db.session.commit()
    db.session.expire_all()
    reino = db.session.get(type(reino), reino.id)
    assert reino.abatimento == prejuizo

    # --- período 2: a casa ganha 100 ---
    p2_inicio = agora() + timedelta(hours=2)
    p2_fim = agora() + timedelta(days=2)
    rodada = _perder(cidadao, "75.00")
    db.session.execute(
        db.update(RodadaMines)
        .where(RodadaMines.id == rodada.id)
        .values(criada_em=p2_inicio + timedelta(minutes=1))
    )
    db.session.commit()
    db.session.expire_all()
    reino = db.session.get(type(reino), reino.id)

    conta = previsao(reino, p2_inicio, p2_fim)
    assert conta["lucro"] == Decimal("75.00")
    assert conta["abatimento_usado"] == prejuizo
    assert conta["lucro_tributavel"] == Decimal("75.00") - prejuizo
    esperado = (Decimal("75.00") - prejuizo) * Decimal("10.00") / Decimal("100")
    assert conta["imposto"] == esperado.quantize(Decimal("0.01"))

    linha = liquidar(reino, p2_inicio, p2_fim, dono)
    db.session.commit()
    db.session.expire_all()

    assert linha.abatimento_usado == prejuizo
    # O saldo se esgotou: o prejuízo era menor que o lucro.
    assert db.session.get(type(reino), reino.id).abatimento == ZERO
    assert conservacao() == antes
    assert _auditoria_fecha()


def test_o_abatimento_maior_que_o_lucro_sobra_para_o_proximo(app, bc, cena):
    reino, dono = cena["reino"], cena["dono"]
    reino.abatimento = Decimal("100.00")
    db.session.commit()
    inicio, fim = _janela()
    _perder(cena["cidadao"], "30.00")

    linha = liquidar(reino, inicio, fim, dono)
    db.session.commit()
    db.session.expire_all()

    assert linha.abatimento_usado == Decimal("30.00")
    assert linha.lucro_tributavel == ZERO
    assert linha.imposto == ZERO
    assert db.session.get(type(reino), reino.id).abatimento == Decimal("70.00")


def test_liquidar_duas_vezes_nao_consome_o_abatimento_duas_vezes(app, bc, cena):
    """O saldo anda junto com o pagamento, sob a mesma guarda de status."""
    reino, dono = cena["reino"], cena["dono"]
    reino.abatimento = Decimal("40.00")
    db.session.commit()
    inicio, fim = _janela()
    _perder(cena["cidadao"], "75.00")

    liquidar(reino, inicio, fim, dono)
    db.session.commit()
    db.session.expire_all()
    depois = db.session.get(type(reino), reino.id).abatimento
    assert depois == ZERO

    with pytest.raises(ValorInvalido):
        liquidar(reino, inicio, fim, dono)
    db.session.rollback()

    db.session.expire_all()
    assert db.session.get(type(reino), reino.id).abatimento == depois


def test_a_liquidacao_guarda_a_conta_inteira(app, bc, cena):
    """Para "por que o imposto foi esse?" não exigir refazer a soma."""
    reino, dono = cena["reino"], cena["dono"]
    reino.abatimento = Decimal("20.00")
    db.session.commit()
    inicio, fim = _janela()
    _perder(cena["cidadao"], "75.00")

    linha = liquidar(reino, inicio, fim, dono)
    db.session.commit()

    assert linha.lucro_bruto == Decimal("75.00")
    assert linha.abatimento_usado == Decimal("20.00")
    assert linha.lucro_tributavel == Decimal("55.00")
    assert linha.aliquota == Decimal("10.00")
    assert linha.imposto == Decimal("5.50")


# --- o panorama da tela -----------------------------------------------------


def test_o_panorama_separa_por_reino_e_fora_de_reino(app, bc, cena):
    inicio, fim = _janela()
    _perder(cena["cidadao"], "50.00")
    _perder(cena["forasteiro"], "30.00")

    visao = panorama(inicio, fim)

    (reino, conta), = visao["reinos"]
    assert reino.id == cena["reino"].id
    assert conta["lucro"] == Decimal("50.00")
    assert conta["imposto"] == Decimal("5.00")
    assert visao["fora_de_reino"] == Decimal("30.00")
