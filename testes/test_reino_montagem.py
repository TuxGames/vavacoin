"""Como o reino chega em produção: CLI, interruptor e link no menu.

O domínio já existia inteiro e não havia como usá-lo: nenhum comando criava
reino, nada ligava a página e nenhum link levava até ela. Estes testes são os
três buracos tapados, e existem para que fechar um deles de novo quebre.
"""

import pytest
from conftest import conservacao

from vavacoin.extensoes import db
from vavacoin.limite import limpar_tudo
from vavacoin.modelos import (
    CHAVE_REINOS_VISIVEIS,
    RegistroAdministrativo,
    Usuario,
    config_ligada,
    definir_config,
)
from vavacoin.reinos import criar_reino, definir_operador, eh_operador, reino_por_nome

SENHA = "senha-boa-123"
SENHA_BC = "senha-do-painel"


@pytest.fixture(autouse=True)
def limite_limpo():
    limpar_tudo()
    yield
    limpar_tudo()


def _rodar(app, *args):
    return app.test_cli_runner().invoke(args=list(args))


def _entrar(app, nome, senha=SENHA):
    cliente = app.test_client()
    cliente.post(
        "/entrar", data={"nome_usuario": nome, "senha": senha}, follow_redirects=True
    )
    return cliente


def _painel(app, bc):
    bc.definir_senha(SENHA_BC)
    db.session.commit()
    return _entrar(app, "banco_central", SENHA_BC)


# --- criar o reino ----------------------------------------------------------


def test_criar_reino_pela_cli(app, bc):
    """O cofre nasce junto, com saldo zero e sem senha."""
    antes = conservacao()

    resultado = _rodar(app, "criar-reino", "Alfheim")

    assert resultado.exit_code == 0, resultado.output
    reino = reino_por_nome("alfheim")
    assert reino is not None
    assert reino.nome == "Alfheim"
    assert reino.cofre.eh_cofre
    assert reino.cofre.saldo == 0
    assert reino.cofre.senha_hash is None
    assert reino.cofre.is_active is False
    # Criar reino não cunha nada: o cofre entra com zero e o dinheiro chega
    # depois por ajuste do Banco Central, como o de todo mundo.
    assert conservacao() == antes


def test_criar_reino_duas_vezes_nao_duplica(app, bc):
    """Idempotente: rodar de novo devolve o mesmo reino e o mesmo cofre."""
    _rodar(app, "criar-reino", "Alfheim")
    primeiro = reino_por_nome("alfheim")
    cofres = db.session.query(Usuario).filter_by(eh_cofre=True).count()

    resultado = _rodar(app, "criar-reino", "Alfheim")

    assert resultado.exit_code == 0, resultado.output
    db.session.expire_all()
    assert reino_por_nome("alfheim").id == primeiro.id
    assert db.session.query(Usuario).filter_by(eh_cofre=True).count() == cofres


def test_criar_reino_sem_nome_e_recusado(app, bc):
    resultado = _rodar(app, "criar-reino", "   ")
    assert resultado.exit_code != 0
    assert "nome" in resultado.output


def test_dois_reinos_convivem(app, bc):
    """Genérico desde o primeiro dia: o segundo não mexe no primeiro."""
    _rodar(app, "criar-reino", "Alfheim")
    _rodar(app, "criar-reino", "Vanaheim")

    db.session.expire_all()
    assert reino_por_nome("alfheim") is not None
    assert reino_por_nome("vanaheim") is not None
    assert reino_por_nome("alfheim").cofre_id != reino_por_nome("vanaheim").cofre_id


# --- nomear o operador ------------------------------------------------------


def test_nomear_operador_pela_cli(app, bc, nova_pessoa):
    _rodar(app, "criar-reino", "Alfheim")
    rei = nova_pessoa(nome="rei")
    db.session.commit()

    resultado = _rodar(app, "operador-reino", "Alfheim", "rei")

    assert resultado.exit_code == 0, resultado.output
    db.session.expire_all()
    assert eh_operador(reino_por_nome("alfheim"), rei)


def test_nomear_o_mesmo_operador_duas_vezes_nao_duplica(app, bc, nova_pessoa):
    from vavacoin.modelos import OperadorDoReino

    _rodar(app, "criar-reino", "Alfheim")
    rei = nova_pessoa(nome="rei")
    db.session.commit()

    _rodar(app, "operador-reino", "Alfheim", "rei")
    resultado = _rodar(app, "operador-reino", "Alfheim", "rei")

    assert resultado.exit_code == 0, resultado.output
    assert "operava" in resultado.output
    db.session.expire_all()
    assert db.session.query(OperadorDoReino).count() == 1


def test_conta_de_sistema_nao_vira_operador_pela_cli(app, bc):
    """O cofre não manda em si mesmo, e o Banco Central não é rei de ninguém."""
    _rodar(app, "criar-reino", "Alfheim")
    db.session.expire_all()
    reino = reino_por_nome("alfheim")

    for conta in [reino.cofre.nome_usuario, "banco_central"]:
        resultado = _rodar(app, "operador-reino", "Alfheim", conta)
        assert resultado.exit_code != 0, conta
        assert "sistema" in resultado.output

    db.session.expire_all()
    assert not eh_operador(reino_por_nome("alfheim"), reino.cofre)


def test_operador_de_reino_inexistente_e_recusado(app, bc, nova_pessoa):
    nova_pessoa(nome="rei")
    db.session.commit()
    resultado = _rodar(app, "operador-reino", "Asgard", "rei")
    assert resultado.exit_code != 0
    assert "inexistente" in resultado.output


def test_conta_inexistente_e_recusada(app, bc):
    _rodar(app, "criar-reino", "Alfheim")
    resultado = _rodar(app, "operador-reino", "Alfheim", "ninguem")
    assert resultado.exit_code != 0
    assert "inexistente" in resultado.output


def test_listar_e_tirar_o_operador(app, bc, nova_pessoa):
    """Sem nome, lista quem opera. Com --tirar, o papel some."""
    _rodar(app, "criar-reino", "Alfheim")
    rei = nova_pessoa(nome="rei")
    db.session.commit()

    vazio = _rodar(app, "operador-reino", "Alfheim")
    assert "operador" in vazio.output

    _rodar(app, "operador-reino", "Alfheim", "rei")
    listado = _rodar(app, "operador-reino", "Alfheim")
    assert "rei" in listado.output

    _rodar(app, "operador-reino", "Alfheim", "rei", "--tirar")
    db.session.expire_all()
    assert not eh_operador(reino_por_nome("alfheim"), rei)


# --- o interruptor no painel ------------------------------------------------


def test_a_pagina_nasce_desligada(app, bc):
    """Nasce fechada: o reino aparece quando o Banco Central mandar."""
    assert config_ligada(CHAVE_REINOS_VISIVEIS) is False


def test_o_painel_liga_e_desliga_a_pagina(app, bc):
    painel = _painel(app, bc)

    painel.post("/painel/reinos", data={"visiveis": "y"}, follow_redirects=True)
    db.session.expire_all()
    assert config_ligada(CHAVE_REINOS_VISIVEIS) is True

    painel.post("/painel/reinos", data={}, follow_redirects=True)
    db.session.expire_all()
    assert config_ligada(CHAVE_REINOS_VISIVEIS) is False


def test_ligar_a_pagina_fica_registrado(app, bc):
    """God mode deixa rastro: quem ligou, quando."""
    _painel(app, bc).post(
        "/painel/reinos", data={"visiveis": "y"}, follow_redirects=True
    )

    registro = (
        db.session.query(RegistroAdministrativo)
        .filter_by(acao="reino")
        .order_by(RegistroAdministrativo.id.desc())
        .first()
    )
    assert registro is not None
    assert registro.detalhe == "visível"
    assert registro.ator_id == bc.id


def test_so_o_banco_central_mexe_no_interruptor(app, bc, nova_pessoa):
    nova_pessoa(nome="ana")
    db.session.commit()

    resposta = _entrar(app, "ana").post("/painel/reinos", data={"visiveis": "y"})

    assert resposta.status_code == 403
    assert config_ligada(CHAVE_REINOS_VISIVEIS) is False


# --- o link no menu, e a rota junto -----------------------------------------


def test_desligada_o_menu_nao_mostra_e_a_rota_nao_abre(app, bc, nova_pessoa):
    """Esconder o link sem fechar a rota seria meio caminho."""
    criar_reino("Alfheim", autoridade=bc)
    nova_pessoa(nome="ana", saldo="10.00")
    db.session.commit()
    cliente = _entrar(app, "ana")

    assert "/reino" not in cliente.get("/carteira").get_data(as_text=True)
    assert cliente.get("/reino/").status_code == 404
    assert cliente.get("/reino/alfheim").status_code == 404


def test_ligada_o_menu_mostra_e_a_rota_abre(app, bc, nova_pessoa):
    criar_reino("Alfheim", autoridade=bc)
    nova_pessoa(nome="ana", saldo="10.00")
    definir_config(CHAVE_REINOS_VISIVEIS, True)
    db.session.commit()
    cliente = _entrar(app, "ana")

    assert "/reino" in cliente.get("/carteira").get_data(as_text=True)
    assert cliente.get("/reino/alfheim").status_code == 200


def test_o_banco_central_ve_o_reino_mesmo_desligado(app, bc):
    """Ele liga o interruptor; precisa conferir a tela antes."""
    criar_reino("Alfheim", autoridade=bc)
    db.session.commit()
    painel = _painel(app, bc)

    assert "/reino" in painel.get("/carteira").get_data(as_text=True)
    assert painel.get("/reino/alfheim").status_code == 200


def test_desligada_fecha_ate_as_rotas_de_divida(app, bc, nova_pessoa):
    """O portão é do blueprint: as rotas sem nome de reino fecham junto.

    São as que um gate escrito tela a tela deixaria passar — ``pagar``,
    ``negociar`` e ``perdoar`` não recebem o nome do reino no caminho.
    """
    from vavacoin.modelos import Cobranca
    from vavacoin.reinos import cobrar, entrar_no_reino

    reino = criar_reino("Alfheim", autoridade=bc)
    rei = nova_pessoa(nome="rei")
    ana = nova_pessoa(nome="ana", saldo="100.00")
    definir_operador(reino, rei, autoridade=bc)
    entrar_no_reino(reino, ana)
    db.session.commit()
    _, criadas = cobrar(reino, rei, Cobranca.ABSOLUTA, "10.00", "imposto")
    db.session.commit()
    divida_id = criadas[0].id

    antes = conservacao()
    cliente = _entrar(app, "ana")
    for rota in [
        f"/reino/divida/{divida_id}/pagar",
        f"/reino/divida/{divida_id}/negociar",
        f"/reino/divida/{divida_id}/perdoar",
    ]:
        assert cliente.post(rota).status_code == 404, rota

    db.session.expire_all()
    assert conservacao() == antes


def test_deslogado_nao_entra_no_reino(app, bc):
    criar_reino("Alfheim", autoridade=bc)
    definir_config(CHAVE_REINOS_VISIVEIS, True)
    db.session.commit()

    resposta = app.test_client().get("/reino/alfheim", follow_redirects=False)

    assert resposta.status_code == 302
    assert "/entrar" in resposta.headers["Location"]
