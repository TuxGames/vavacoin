"""O ranking geral: todo mundo, com o reino ao lado do nome.

A tabela por reino continua existindo — são duas telas. O que **não** são
dois é a conta das posições: as duas chamam ``ranquear``. Duas implementações
da mesma regra de privacidade divergem, e o dia em que divergirem é o dia em
que vaza — por isso ``test_as_duas_telas_usam_a_mesma_conta`` existe.
"""

import pytest

from vavacoin.caladinho import criar_casa
from vavacoin.extensoes import db
from vavacoin.limite import limpar_tudo
from vavacoin.modelos import (
    CHAVE_RANKING_VISIVEL,
    CHAVE_REINOS_VISIVEIS,
    Usuario,
    definir_config,
)
from vavacoin.operacoes import encerrar_conta
from vavacoin.ranking import gente, ranquear, reinos_de
from vavacoin.reinos import (
    criar_reino,
    definir_operador,
    entrar_no_reino,
    ranking_de_cidadaos,
)

SENHA = "senha-boa-123"
SENHA_BC = "senha-do-painel"


@pytest.fixture(autouse=True)
def limite_limpo():
    limpar_tudo()
    yield
    limpar_tudo()


@pytest.fixture
def turma(app, bc, nova_pessoa):
    """Gente com e sem reino, mais as contas que não são gente."""
    reino = criar_reino("Alfheim", autoridade=bc)
    db.session.commit()
    criar_casa(autoridade=bc)
    db.session.commit()

    ana = nova_pessoa(nome="ana", saldo="123.45")
    bia = nova_pessoa(nome="bia", saldo="678.90")
    caio = nova_pessoa(nome="caio", saldo="50.00")
    saiu = nova_pessoa(nome="saiu", saldo="0.00")
    db.session.commit()

    ana.nome_exibicao = "Aninha"
    bia.nome_exibicao = "Bianca"
    caio.nome_exibicao = "Caetano"
    entrar_no_reino(reino, ana)
    entrar_no_reino(reino, bia)
    db.session.commit()

    encerrar_conta(saiu, "saiu do jogo", autoridade=bc)
    definir_config(CHAVE_REINOS_VISIVEIS, True)
    bc.definir_senha(SENHA_BC)
    db.session.commit()
    return {"reino": reino, "ana": ana, "bia": bia, "caio": caio, "saiu": saiu}


def _entrar(app, nome, senha=SENHA):
    cliente = app.test_client()
    cliente.post(
        "/entrar", data={"nome_usuario": nome, "senha": senha}, follow_redirects=True
    )
    return cliente


# --- quem entra na lista ----------------------------------------------------


def test_o_ranking_geral_pega_quem_nao_e_de_reino_nenhum(app, bc, turma):
    """É a diferença para a tabela do reino: o caio entra."""
    nomes = [p.nome_usuario for p in gente()]

    assert "caio" in nomes
    assert "ana" in nomes and "bia" in nomes


def test_contas_de_sistema_ficam_de_fora(app, bc, turma):
    """Banco Central, cofre de reino e casa do cassino não são gente."""
    nomes = [p.nome_usuario for p in gente()]

    assert "banco_central" not in nomes
    assert turma["reino"].cofre.nome_usuario not in nomes
    assert "caladinho" not in nomes


def test_conta_encerrada_fica_de_fora(app, bc, turma):
    assert "saiu" not in [p.nome_usuario for p in gente()]


# --- o reino ao lado do nome ------------------------------------------------


def test_cada_pessoa_vem_com_o_reino_atual(app, bc, turma):
    mapa = reinos_de(gente())

    assert mapa[turma["ana"].id].nome == "Alfheim"
    assert mapa[turma["caio"].id] is None, "sem reino é None, e a tela rotula"


def test_o_reino_mostrado_e_o_de_agora_nao_o_congelado(app, bc, turma):
    """Rótulo de tela, não conta de imposto: quem entrou hoje aparece hoje."""
    caio = turma["caio"]
    assert reinos_de([caio])[caio.id] is None

    entrar_no_reino(turma["reino"], caio)
    db.session.commit()

    assert reinos_de([caio])[caio.id].nome == "Alfheim"


def test_a_tela_rotula_quem_nao_tem_reino(app, bc, turma):
    corpo = _entrar(app, "caio").get("/ranking").get_data(as_text=True)

    assert "sem reino" in corpo
    assert "Alfheim" in corpo


# --- a mesma regra de privacidade, sem exceção ------------------------------


def test_quem_escondeu_nao_e_posicionado_no_geral(app, bc, turma):
    """A posição vaza o valor, aqui como na tabela do reino."""
    turma["bia"].saldo_publico = False
    db.session.commit()

    ranking, escondidos = ranquear(gente())

    assert [p.nome_usuario for _, p in ranking] == ["ana", "caio"]
    assert [p.nome_usuario for p in escondidos] == ["bia"]


def test_as_posicoes_nao_tem_buracos_no_geral(app, bc, turma):
    turma["bia"].saldo_publico = False
    db.session.commit()

    ranking, _ = ranquear(gente())

    assert [posicao for posicao, _ in ranking] == [1, 2]


def test_as_posicoes_sao_iguais_ate_para_o_banco_central(app, bc, turma):
    """Se o BC visse outra ordem, a diferença diria quem escondeu o quê."""
    turma["bia"].saldo_publico = False
    db.session.commit()

    da_turma = _entrar(app, "caio").get("/ranking").get_data(as_text=True)
    do_bc = _entrar(app, "banco_central", SENHA_BC).get("/ranking").get_data(
        as_text=True
    )

    assert da_turma.index("Aninha") < da_turma.index("Bianca")
    assert do_bc.index("Aninha") < do_bc.index("Bianca")


def test_o_bc_ve_o_numero_do_escondido_mas_nunca_a_posicao(app, bc, turma):
    turma["bia"].saldo_publico = False
    db.session.commit()

    corpo = _entrar(app, "banco_central", SENHA_BC).get("/ranking").get_data(
        as_text=True
    )

    assert "678.90" in corpo
    ranking, _ = ranquear(gente())
    assert "bia" not in [p.nome_usuario for _, p in ranking]


def test_quem_escondeu_aparece_com_nome_e_reino(app, bc, turma):
    """Some o número e a posição, não a pessoa nem o reino dela."""
    turma["bia"].saldo_publico = False
    db.session.commit()

    corpo = _entrar(app, "caio").get("/ranking").get_data(as_text=True)

    assert "Bianca" in corpo
    assert "678.90" not in corpo


def test_as_duas_telas_usam_a_mesma_conta(app, bc, turma):
    """A garantia de que a regra não vai divergir: é uma função só.

    O ranking do reino e o geral produzem a mesma ordem para o mesmo conjunto
    de gente — porque os dois chamam ``ranquear``.
    """
    turma["bia"].saldo_publico = False
    db.session.commit()

    do_reino, escondidos_do_reino = ranking_de_cidadaos(turma["reino"])
    do_geral, escondidos_do_geral = ranquear(
        [p for p in gente() if p.id in (turma["ana"].id, turma["bia"].id)]
    )

    assert [(n, p.id) for n, p in do_reino] == [(n, p.id) for n, p in do_geral]
    assert [p.id for p in escondidos_do_reino] == [p.id for p in escondidos_do_geral]


# --- o interruptor ----------------------------------------------------------


def test_o_ranking_nasce_ligado(app, bc, turma):
    """Ao contrário dos reinos: é o que o dono quer usar agora."""
    assert _entrar(app, "caio").get("/ranking").status_code == 200


def test_desligado_a_rota_fecha(app, bc, turma):
    """Esconder o link não é a tranca."""
    definir_config(CHAVE_RANKING_VISIVEL, False)
    db.session.commit()

    assert _entrar(app, "caio").get("/ranking").status_code == 404


def test_desligado_o_link_some_da_nav(app, bc, turma):
    definir_config(CHAVE_RANKING_VISIVEL, False)
    db.session.commit()

    corpo = _entrar(app, "caio").get("/").get_data(as_text=True)

    assert "/ranking" not in corpo


def test_o_banco_central_ve_mesmo_desligado(app, bc, turma):
    """É ele quem liga, e precisa conferir a tela antes."""
    definir_config(CHAVE_RANKING_VISIVEL, False)
    db.session.commit()

    resposta = _entrar(app, "banco_central", SENHA_BC).get("/ranking")

    assert resposta.status_code == 200


def test_deslogado_nao_ve_o_ranking(app, bc, turma):
    resposta = app.test_client().get("/ranking", follow_redirects=False)

    assert resposta.status_code == 302
    assert "/entrar" in resposta.headers["Location"]


def test_o_painel_liga_e_desliga(app, bc, turma):
    painel = _entrar(app, "banco_central", SENHA_BC)

    painel.post("/painel/ranking", data={}, follow_redirects=True)
    db.session.expire_all()
    assert _entrar(app, "caio").get("/ranking").status_code == 404

    painel.post("/painel/ranking", data={"visivel": "y"}, follow_redirects=True)
    db.session.expire_all()
    assert _entrar(app, "caio").get("/ranking").status_code == 200
