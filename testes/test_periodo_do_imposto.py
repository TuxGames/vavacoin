"""Os períodos do imposto do cassino são um mosaico: sem sobra e sem sobreposição.

Dois erros espelhados, e os dois custam dinheiro de alguém:

- **Sobrepor cobra duas vezes.** Era o bug em produção. O padrão da tela era
  "últimos 30 dias terminando agora", e o ``fim`` mudava a cada visita — então
  a segunda liquidação, feita do jeito natural, recobria quase todo o período
  da primeira. O ``UNIQUE (reino, início, fim)`` não pegava: ele só barra o
  intervalo **idêntico**. Sai do caixa do cassino para o cofre do reino.

- **Deixar vão nunca cobra.** Mais silencioso: o lucro do meio não é de
  ninguém, e ninguém reclama de um imposto que não veio.

Por isso o começo do período não é escolhido — é derivado da última
liquidação daquele reino. A tela sugere; ``liquidar`` garante.
"""

from datetime import timedelta
from decimal import Decimal

import pytest

from vavacoin.auditoria import auditar
from vavacoin.caladinho import criar_casa, criar_rodada, definir_dono, revelar_casa
from vavacoin.dinheiro import ZERO
from vavacoin.erros import ValorInvalido
from vavacoin.extensoes import db
from vavacoin.limite import limpar_tudo
from vavacoin.modelos import RodadaMines, agora, com_fuso
from vavacoin.operacoes import ajustar_saldo
from vavacoin.reinos import criar_reino, definir_operador, entrar_no_reino
from vavacoin.tributo import (
    fim_efetivo,
    inicio_do_periodo,
    liquidar,
    lucro_do_periodo,
    previsao,
    ultima_liquidacao,
)

from conftest import conservacao

SENHA = "senha-boa-123"


@pytest.fixture(autouse=True)
def limite_limpo():
    limpar_tudo()
    yield
    limpar_tudo()


@pytest.fixture
def cena(app, bc, nova_pessoa):
    """Caixa grande o bastante para uma aposta de 100 (o teto é caixa ÷ 50)."""
    casa = criar_casa(autoridade=bc)
    db.session.commit()
    ajustar_saldo(casa, "6000.00", "caixa", autoridade=bc)
    db.session.commit()

    dono = nova_pessoa(nome="gustavo", saldo="50.00")
    definir_dono(dono, autoridade=bc)
    db.session.commit()

    reino = criar_reino("Alfheim", autoridade=bc)
    db.session.commit()
    rei = nova_pessoa(nome="rei", saldo="20.00")
    definir_operador(reino, rei, autoridade=bc)
    db.session.commit()

    cidadao = nova_pessoa(nome="ana", saldo="300.00")
    entrar_no_reino(reino, cidadao)
    db.session.commit()

    return {"casa": casa, "dono": dono, "reino": reino, "cidadao": cidadao}


def _perder(jogador, aposta):
    """Uma rodada perdida: a casa fica com a aposta inteira."""
    rodada = criar_rodada(jogador, aposta, minas_escolhidas=24)
    db.session.commit()
    revelar_casa(jogador, rodada.casas_com_mina[0])
    db.session.commit()
    return rodada


def _datar(rodada, momento):
    db.session.execute(
        db.update(RodadaMines)
        .where(RodadaMines.id == rodada.id)
        .values(criada_em=momento)
    )
    db.session.commit()
    return rodada


def _recarregar(reino):
    db.session.expire_all()
    return db.session.get(type(reino), reino.id)


def _auditoria_fecha():
    relatorio = auditar()
    assert relatorio["ok"], relatorio
    assert relatorio["ledger"]["saldos_divergentes"] == []
    return True


# --- o caso exato que estava em produção ------------------------------------


def test_periodos_sobrepostos_nao_cobram_o_mesmo_lucro_duas_vezes(app, bc, cena):
    """100,00 de lucro, dois períodos sobrepostos: 10,00 de imposto, não 20,00.

    Antes da correção o cofre terminava com 20,00 — 20% numa alíquota de 10%,
    saindo do caixa do cassino.
    """
    reino, dono, cidadao = cena["reino"], cena["dono"], cena["cidadao"]
    antes = conservacao()

    _datar(_perder(cidadao, "100.00"), agora() - timedelta(days=10))
    reino = _recarregar(reino)

    p1 = (agora() - timedelta(days=30), agora() - timedelta(days=5))
    liquidar(reino, *p1, dono)
    db.session.commit()
    reino = _recarregar(reino)
    assert reino.cofre.saldo == Decimal("10.00")

    # O segundo período do jeito natural da tela: "últimos 20 dias até agora".
    # Ele recobre a rodada, que já foi cobrada.
    p2 = (agora() - timedelta(days=20), agora())
    with pytest.raises(ValorInvalido) as recusa:
        liquidar(reino, *p2, dono)
    db.session.rollback()

    assert "cruza um já liquidado" in str(recusa.value)
    assert _recarregar(reino).cofre.saldo == Decimal("10.00")
    assert conservacao() == antes
    assert _auditoria_fecha()


def test_o_periodo_correto_depois_do_primeiro_nao_recobra(app, bc, cena):
    """O caminho que a tela agora oferece: começar onde o anterior parou."""
    reino, dono, cidadao = cena["reino"], cena["dono"], cena["cidadao"]

    _datar(_perder(cidadao, "100.00"), agora() - timedelta(days=10))
    reino = _recarregar(reino)
    liquidar(reino, agora() - timedelta(days=30), agora() - timedelta(days=5), dono)
    db.session.commit()
    reino = _recarregar(reino)

    linha = liquidar(reino, inicio_do_periodo(reino), agora(), dono)
    db.session.commit()

    assert linha.lucro_bruto == ZERO, "não sobrou lucro para cobrar de novo"
    assert _recarregar(reino).cofre.saldo == Decimal("10.00")
    assert _auditoria_fecha()


# --- o buraco espelhado: vão entre liquidações ------------------------------


def test_nao_sobra_pedaco_nao_tributado_entre_liquidacoes(app, bc, cena):
    """Três rodadas, três períodos consecutivos: tudo é cobrado uma vez.

    A soma dos lucros liquidados tem de bater com o lucro do intervalo
    inteiro. Se sobrasse vão, a soma viria menor — e ninguém notaria, porque
    imposto que não chega não reclama.
    """
    reino, dono, cidadao = cena["reino"], cena["dono"], cena["cidadao"]
    comeco = inicio_do_periodo(reino)

    for minutos, valor in ((30, "100.00"), (20, "50.00"), (10, "30.00")):
        _datar(_perder(cidadao, valor), agora() - timedelta(minutes=minutos))
    reino = _recarregar(reino)

    # Três liquidações seguidas, cada uma começando onde a anterior parou.
    liquidado = ZERO
    for _ in range(3):
        reino = _recarregar(reino)
        linha = liquidar(reino, inicio_do_periodo(reino), agora(), dono)
        db.session.commit()
        liquidado += linha.lucro_bruto
        _perder(cidadao, "20.00")
        db.session.commit()

    reino = _recarregar(reino)
    fim = ultima_liquidacao(reino).fim
    total = lucro_do_periodo(reino.id, comeco, com_fuso(fim))

    assert liquidado == total, "algum pedaço ficou de fora"
    assert _auditoria_fecha()


def test_comecar_depois_do_fim_anterior_e_recusado(app, bc, cena):
    """O vão de propósito: pular uma semana para não cobrar por ela."""
    reino, dono, cidadao = cena["reino"], cena["dono"], cena["cidadao"]

    _datar(_perder(cidadao, "100.00"), agora() - timedelta(days=10))
    reino = _recarregar(reino)
    liquidar(reino, agora() - timedelta(days=30), agora() - timedelta(days=20), dono)
    db.session.commit()
    reino = _recarregar(reino)

    with pytest.raises(ValorInvalido) as recusa:
        liquidar(reino, inicio_do_periodo(reino) + timedelta(days=3), agora(), dono)
    db.session.rollback()

    assert "não é cobrado de ninguém" in str(recusa.value)


def test_o_primeiro_periodo_comeca_na_criacao_do_reino(app, bc, cena):
    """Sem liquidação anterior, o começo é o nascimento do reino.

    Assim a primeira liquidação não pula nada: antes disso não existe rodada
    atribuída a ele.
    """
    reino = cena["reino"]

    assert ultima_liquidacao(reino) is None
    assert inicio_do_periodo(reino) == com_fuso(reino.criado_em)


def test_o_periodo_seguinte_encosta_no_anterior(app, bc, cena):
    reino, dono, cidadao = cena["reino"], cena["dono"], cena["cidadao"]
    _datar(_perder(cidadao, "100.00"), agora() - timedelta(days=10))
    reino = _recarregar(reino)

    fim = agora() - timedelta(days=1)
    liquidar(reino, agora() - timedelta(days=30), fim, dono)
    db.session.commit()
    reino = _recarregar(reino)

    assert inicio_do_periodo(reino) == com_fuso(fim)


# --- o fim no futuro --------------------------------------------------------


def test_o_fim_no_futuro_e_aparado_para_agora(app, bc, cena):
    """Liquidar até o ano que vem engoliria de véspera o lucro até lá."""
    reino, dono, cidadao = cena["reino"], cena["dono"], cena["cidadao"]
    _datar(_perder(cidadao, "100.00"), agora() - timedelta(days=10))
    reino = _recarregar(reino)

    linha = liquidar(
        reino, agora() - timedelta(days=30), agora() + timedelta(days=365), dono
    )
    db.session.commit()

    assert com_fuso(linha.fim) <= agora()
    assert com_fuso(inicio_do_periodo(_recarregar(reino))) <= agora()


def test_fim_efetivo_nao_mexe_no_passado(app, bc, cena):
    passado = agora() - timedelta(days=2)
    assert fim_efetivo(passado) == passado


# --- a recusa é do servidor, não da tela ------------------------------------


def test_a_rota_ignora_o_inicio_mandado_pela_url(app, bc, cena):
    """A tela pode sugerir; quem decide o começo é a operação.

    Aqui a URL manda um começo que recobriria a liquidação anterior. O
    resultado tem de ser o mesmo de mandar o certo — porque a rota nem lê
    esse parâmetro.
    """
    reino, dono, cidadao = cena["reino"], cena["dono"], cena["cidadao"]
    _datar(_perder(cidadao, "100.00"), agora() - timedelta(days=10))
    reino = _recarregar(reino)
    liquidar(reino, agora() - timedelta(days=30), agora() - timedelta(days=5), dono)
    db.session.commit()
    reino = _recarregar(reino)
    cofre_antes = reino.cofre.saldo

    dono.definir_senha(SENHA)
    db.session.commit()
    cliente = app.test_client()
    cliente.post(
        "/entrar",
        data={"nome_usuario": "gustavo", "senha": SENHA},
        follow_redirects=True,
    )

    def liquidar_pela_rota(inicio):
        return cliente.post(
            f"/caladinho/casa/imposto/{reino.id}"
            f"?inicio={inicio.isoformat()}&fim={agora().isoformat()}",
            follow_redirects=True,
        )

    # O começo mandado recobriria a liquidação anterior. Nada se move.
    resposta = liquidar_pela_rota(agora() - timedelta(days=30))
    db.session.expire_all()
    assert resposta.status_code == 200
    assert _recarregar(reino).cofre.saldo == cofre_antes

    # Controle positivo: com lucro novo, a MESMA URL errada liquida — e
    # liquida só o lucro novo. Sem isto, o teste acima passaria até se a rota
    # não existisse.
    _perder(cidadao, "100.00")
    db.session.commit()
    liquidar_pela_rota(agora() - timedelta(days=30))
    db.session.expire_all()

    assert _recarregar(reino).cofre.saldo == cofre_antes + Decimal("10.00")
    assert _auditoria_fecha()


def test_a_previsao_da_tela_usa_o_periodo_do_reino(app, bc, cena):
    """O que a tela mostra é o que a liquidação vai gravar."""
    reino, dono, cidadao = cena["reino"], cena["dono"], cena["cidadao"]
    _datar(_perder(cidadao, "100.00"), agora() - timedelta(days=10))
    reino = _recarregar(reino)
    liquidar(reino, agora() - timedelta(days=30), agora() - timedelta(days=5), dono)
    db.session.commit()
    reino = _recarregar(reino)

    conta = previsao(reino, inicio_do_periodo(reino), agora())

    assert conta["inicio"] == inicio_do_periodo(reino)
    assert conta["lucro"] == ZERO
