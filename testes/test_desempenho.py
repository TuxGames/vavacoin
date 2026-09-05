"""Velocidade: menos viagens ao servidor e menos consultas por tela.

O dono relatou lag. De fora dá para medir que o servidor responde rápido por
requisição — o custo estava em **quantas** requisições cada interação pede e
quanto cada uma ocupa o worker único do plano grátis.

Três coisas são travadas aqui, porque as três voltam sozinhas na primeira
distração:

1. **A configuração é lida uma vez por requisição.** Era uma consulta por
   chave, sete a dez por página, mais duas do menu em toda tela do site.
2. **Nada de N+1 nas listas.** A tela de operar fazia uma consulta por cidadão
   e uma por dívida.
3. **O clique no jogo é uma viagem, não duas** — e o caminho sem JavaScript
   continua sendo o POST-redirect-GET de sempre.

Nenhum destes testes olha para tempo de relógio: tempo varia com a máquina e o
teste vira alarme falso. O que eles contam é **número de consultas** e
**número de respostas**, que é o que a otimização mudou.
"""

import pytest
from sqlalchemy import event

from vavacoin.auditoria import conferir_ledger
from vavacoin.caladinho import criar_casa, criar_rodada, definir_dono
from vavacoin.extensoes import db
from vavacoin.limite import limpar_tudo
from vavacoin.modelos import (
    CHAVE_REINOS_VISIVEIS,
    RodadaMines,
    config_ligada,
    config_texto,
    definir_config,
)
from vavacoin.operacoes import ajustar_saldo
from vavacoin.reinos import (
    cobrar,
    criar_reino,
    definir_operador,
    eh_operador,
    entrar_no_reino,
    operadores_ids,
    total_devido,
    totais_devidos,
)

from conftest import conservacao

SENHA = "senha-boa-123"


@pytest.fixture(autouse=True)
def limite_limpo():
    limpar_tudo()
    yield
    limpar_tudo()


class Contador:
    """Conta as consultas que saem para o banco enquanto está ligado."""

    def __init__(self):
        self.sqls = []
        self._ligado = False

    def __enter__(self):
        self.sqls = []
        self._ligado = True
        event.listen(db.engine, "before_cursor_execute", self._ouvir)
        return self

    def __exit__(self, *_):
        self._ligado = False
        event.remove(db.engine, "before_cursor_execute", self._ouvir)
        return False

    def _ouvir(self, conn, cursor, instrucao, parametros, contexto, muitos):
        if self._ligado:
            self.sqls.append(" ".join(instrucao.split()))

    def contando(self, pedaco):
        return sum(1 for sql in self.sqls if pedaco in sql)


@pytest.fixture
def turma(app, bc, nova_pessoa):
    reino = criar_reino("Alfheim", autoridade=bc)
    db.session.commit()
    casa = criar_casa(autoridade=bc)
    db.session.commit()
    ajustar_saldo(casa, "3000.00", "caixa", autoridade=bc)
    db.session.commit()

    pessoas = [nova_pessoa(nome=f"aluno{n}", saldo="200.00") for n in range(8)]
    db.session.commit()
    definir_dono(pessoas[0], autoridade=bc)
    definir_operador(reino, pessoas[1], autoridade=bc)
    db.session.commit()
    for pessoa in pessoas[1:]:
        entrar_no_reino(reino, pessoa)
    definir_config(CHAVE_REINOS_VISIVEIS, True)
    db.session.commit()
    cobrar(reino, pessoas[1], "absoluta", "5.00", "imposto", pessoas[2:])
    db.session.commit()
    return {"reino": reino, "pessoas": pessoas, "casa": casa}


def _entrar(app, nome):
    cliente = app.test_client()
    cliente.post(
        "/entrar", data={"nome_usuario": nome, "senha": SENHA}, follow_redirects=True
    )
    return cliente


# --- 1. a configuração é lida uma vez por requisição -------------------------


def test_a_tela_le_a_configuracao_uma_vez_so(app, bc, turma):
    """Era uma consulta por chave; toda tela pergunta várias."""
    cliente = _entrar(app, "aluno5")

    with Contador() as contador:
        cliente.get("/caladinho/mines")

    assert contador.contando("FROM configuracao") <= 1


def test_a_memoria_de_config_nao_atravessa_requisicoes(app, bc, turma):
    """Não é cache: some com a requisição, senão viraria valor velho."""
    cliente = _entrar(app, "aluno5")
    cliente.get("/ranking")

    definir_config(CHAVE_REINOS_VISIVEIS, False)
    db.session.commit()

    corpo = cliente.get("/").get_data(as_text=True)
    assert "/reino/" not in corpo


def test_gravar_config_invalida_a_memoria_na_hora(app, bc, turma):
    """Mesmo dentro da mesma requisição, ler depois de escrever vê o novo."""
    with app.test_request_context("/"):
        assert config_ligada(CHAVE_REINOS_VISIVEIS) is True
        definir_config(CHAVE_REINOS_VISIVEIS, False)
        assert config_ligada(CHAVE_REINOS_VISIVEIS) is False


def test_config_ligada_e_config_texto_leem_o_mesmo(app, bc, turma):
    """Uma leitura só: o interruptor sai do valor cru."""
    with app.test_request_context("/"):
        assert config_ligada(CHAVE_REINOS_VISIVEIS) == (
            config_texto(CHAVE_REINOS_VISIVEIS) == "1"
        )


# --- 2. nada de N+1 ----------------------------------------------------------


def test_operar_nao_consulta_uma_vez_por_cidadao(app, bc, turma):
    """Eram 15 consultas de dívida e 11 de operador numa tela só."""
    cliente = _entrar(app, "aluno1")

    with Contador() as contador:
        resposta = cliente.get("/reino/alfheim/operar")

    assert resposta.status_code == 200
    assert contador.contando("FROM divida") <= 2
    assert contador.contando("FROM operador_do_reino") <= 2


def test_o_total_em_lote_bate_com_o_de_um_por_um(app, bc, turma):
    """A conta é a mesma; o que mudou é quantas vezes se pergunta."""
    reino, pessoas = turma["reino"], turma["pessoas"]

    em_lote = totais_devidos(reino, pessoas)
    um_a_um = {p.id: total_devido(p, reino=reino) for p in pessoas}

    assert em_lote == um_a_um
    assert any(valor > 0 for valor in em_lote.values()), "a cena tem de ter dívida"


def test_o_conjunto_de_operadores_bate_com_a_pergunta_avulsa(app, bc, turma):
    reino, pessoas = turma["reino"], turma["pessoas"]
    ids = operadores_ids(reino)

    for pessoa in pessoas:
        assert (pessoa.id in ids) == eh_operador(reino, pessoa)


def test_o_destino_em_lote_bate_com_o_de_uma_conta(app, bc, turma):
    """Oferecer "apagar" numa conta com histórico é o bug do Benbals."""
    from vavacoin.operacoes import destino_da_conta, destinos_das_contas
    from vavacoin.modelos import Usuario

    contas = list(db.session.execute(db.select(Usuario)).scalars())
    em_lote = destinos_das_contas(contas)

    assert em_lote == {c.id: destino_da_conta(c) for c in contas}


# --- 3. o clique do jogo é uma viagem ---------------------------------------


def _jogar_uma_casa(cliente, rodada, parcial):
    cabecalhos = {"X-VavaCoin-Parcial": "1"} if parcial else {}
    return cliente.post(
        "/caladinho/mines/revelar",
        data={"casa": str(rodada.casas_com_mina[0])},
        headers=cabecalhos,
    )


def test_sem_javascript_continua_o_redirect_de_sempre(app, bc, turma):
    """O caminho antigo é o padrão, não a exceção."""
    jogador = turma["pessoas"][3]
    cliente = _entrar(app, jogador.nome_usuario)
    rodada = criar_rodada(jogador, "2.00", minas_escolhidas=24)
    db.session.commit()

    resposta = _jogar_uma_casa(cliente, rodada, parcial=False)

    assert resposta.status_code == 302
    assert "/caladinho/mines" in resposta.headers["Location"]


def test_com_fetch_a_resposta_ja_e_a_tela(app, bc, turma):
    """Uma viagem em vez de duas: some o redirect e o GET seguinte."""
    jogador = turma["pessoas"][4]
    cliente = _entrar(app, jogador.nome_usuario)
    rodada = criar_rodada(jogador, "2.00", minas_escolhidas=24)
    db.session.commit()

    resposta = _jogar_uma_casa(cliente, rodada, parcial=True)

    assert resposta.status_code == 200
    corpo = resposta.get_data(as_text=True)
    assert 'id="conteudo"' in corpo, "o jogo.js troca este pedaço"
    assert "data-jogo" in corpo


def test_os_dois_caminhos_mostram_a_mesma_rodada(app, bc, turma):
    """O transporte muda; o que a pessoa vê, não.

    É o teste que impede o atalho de virar uma segunda tela de jogo com
    regras próprias.
    """
    antes = conservacao()

    jogador = turma["pessoas"][5]
    cliente = _entrar(app, jogador.nome_usuario)
    rodada = criar_rodada(jogador, "2.00", minas_escolhidas=24)
    db.session.commit()
    do_fetch = _jogar_uma_casa(cliente, rodada, parcial=True).get_data(as_text=True)

    outro = turma["pessoas"][6]
    cliente_dele = _entrar(app, outro.nome_usuario)
    rodada_dele = criar_rodada(outro, "2.00", minas_escolhidas=24)
    db.session.commit()
    _jogar_uma_casa(cliente_dele, rodada_dele, parcial=False)
    do_redirect = cliente_dele.get("/caladinho/mines").get_data(as_text=True)

    # Os dois perderam na mina: as duas telas dizem a mesma coisa.
    assert ("Perdeu" in do_fetch) == ("Perdeu" in do_redirect)
    assert conservacao() == antes
    assert conferir_ledger()["ok"]


def test_o_atalho_nao_deixa_a_rodada_resolver_duas_vezes(app, bc, turma):
    """O mesmo clique repetido continua sendo uma jogada só."""
    jogador = turma["pessoas"][7]
    cliente = _entrar(app, jogador.nome_usuario)
    rodada = criar_rodada(jogador, "2.00", minas_escolhidas=24)
    db.session.commit()
    saldo_antes = jogador.saldo
    antes = conservacao()

    for _ in range(3):
        _jogar_uma_casa(cliente, rodada, parcial=True)
        db.session.expire_all()

    db.session.expire_all()
    assert db.session.get(RodadaMines, rodada.id).encerrada
    assert db.session.get(type(jogador), jogador.id).saldo == saldo_antes
    assert conservacao() == antes
    assert conferir_ledger()["ok"]


def test_o_atalho_nao_abre_jogo_desligado(app, bc, turma):
    """A guarda do jogo vale igual nos dois caminhos."""
    from vavacoin.jogos import definir_ligado

    jogador = turma["pessoas"][3]
    cliente = _entrar(app, jogador.nome_usuario)
    definir_ligado("mines", False, turma["pessoas"][0])
    db.session.commit()

    resposta = cliente.post(
        "/caladinho/mines/comecar",
        data={"aposta": "2.00", "minas": "3"},
        headers={"X-VavaCoin-Parcial": "1"},
    )

    assert resposta.status_code == 404


# --- 4. estáticos com versão no endereço ------------------------------------


def test_o_endereco_do_css_carrega_a_versao(app, bc, turma):
    """Sem versão, cache longo entregaria arquivo velho depois do deploy."""
    corpo = _entrar(app, "aluno5").get("/carteira").get_data(as_text=True)

    assert "base.css?v=" in corpo


def test_a_versao_muda_quando_o_arquivo_muda(app, tmp_path):
    from vavacoin.estaticos import versao

    arquivo = tmp_path / "teste.css"
    arquivo.write_text("a{}", encoding="utf-8")
    primeira = versao(arquivo)

    arquivo.write_text("b{}", encoding="utf-8")

    assert versao(arquivo) != primeira


def test_arquivo_que_nao_existe_nao_derruba_a_tela(app, tmp_path):
    from vavacoin.estaticos import versao

    assert versao(tmp_path / "nao-existe.css") is None
