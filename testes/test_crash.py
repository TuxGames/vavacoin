"""Crash: uma rodada só, do relógio, com o estouro guardado até a hora certa.

O jogo era individual — cada aposta abria a própria curva e o número subia só
na sua tela. Virou compartilhado: a rodada é ``floor(epoch / ciclo)``, o ponto
de estouro é derivado de um segredo do servidor, e as vinte telas animam a
mesma curva sem que o servidor guarde nada por jogador.

Três coisas este arquivo guarda com mais zelo que as outras:

1. **O estouro não pode existir para o cliente enquanto der para apostar.** É
   o ponto de segurança da feature inteira: quem souber onde a rodada explode
   antes de a janela fechar aposta só quando é favorável e ganha sempre. Há
   teste para a tela, para a rota direta, e para a tentativa de perguntar pela
   rodada seguinte.
2. **Não existe saque manual.** É o que faz conhecer a curva ser inofensivo.
3. **Nada fica preso.** Aposta cujo voo acabou é liquidada na leitura de
   qualquer tela, de todo mundo, senão a exposição da casa nunca é devolvida.

Todo teste que mexe em dinheiro passa pelo ``conservacao()``, e o relógio é
sempre o da fixture ``relogio`` — nunca o da máquina.
"""

from decimal import Decimal

import pytest
from conftest import conservacao

from vavacoin.caladinho import (
    criar_casa,
    criar_rodada_crash,
    definir_dono,
    exposicao_comprometida,
    historico_crash,
    liquidar_crash_vencido,
    limite_de_aposta,
    resolver_crash,
    rodada_compartilhada,
    rodada_compartilhada_de_agora,
    segredo_do_crash,
    ultimas_rodadas,
    visao_da_rodada_crash,
    visao_do_ciclo,
)
from vavacoin.crash import (
    ALVO_MINIMO,
    DURACAO_DO_CICLO,
    JANELA_DE_APOSTA,
    SEGUNDOS_PARA_DOBRAR,
    TETO_DO_MULTIPLICADOR,
    VOO_MAXIMO,
    da_para_apostar,
    multiplicador_no_tempo,
    numero_da_rodada,
    ponto_de_estouro_da_rodada,
    segundos_para_multiplicador,
    sortear_ponto_de_estouro,
    uniforme_da_rodada,
    validar_alvo,
)
from vavacoin.erros import ApostaAlta, RodadaEmAndamento, ValorInvalido
from vavacoin.extensoes import db
from vavacoin.jogos import definir_ligado
from vavacoin.limite import limpar_tudo
from vavacoin.modelos import RodadaCrash, RodadaCrashCompartilhada, Transacao
from vavacoin.operacoes import ajustar_saldo
from vavacoin.vantagem import definir_vantagem, fator_de

SENHA = "senha-boa-123"


@pytest.fixture(autouse=True)
def limite_limpo():
    limpar_tudo()
    yield
    limpar_tudo()


@pytest.fixture
def cassino(app, bc, nova_pessoa):
    conta = criar_casa(autoridade=bc)
    db.session.commit()
    ajustar_saldo(conta, "2000.00", "caixa do teste", autoridade=bc)
    db.session.commit()

    gustavo = nova_pessoa(nome="gustavo", saldo="100.00")
    definir_dono(gustavo, autoridade=bc)
    db.session.commit()

    ana = nova_pessoa(nome="ana", saldo="100.00")
    bia = nova_pessoa(nome="bia", saldo="100.00")
    return {"casa": conta, "dono": gustavo, "ana": ana, "bia": bia}


def _entrar(app, nome, senha=SENHA):
    cliente = app.test_client()
    cliente.post(
        "/entrar", data={"nome_usuario": nome, "senha": senha}, follow_redirects=True
    )
    return cliente


def _forcar_estouro(numero, valor):
    """Fixa o ponto de estouro da rodada, na rodada E nas apostas dela.

    A derivação é testada à parte; aqui o que importa é o que acontece DEPOIS
    de o número existir, e escrevê-lo à mão é o que deixa o teste falar de um
    caso (ganhou, perdeu) em vez de depender do sorteio.

    Escreve nos dois lugares porque a aposta guarda a **própria cópia** do
    estouro, congelada quando entrou — pela mesma razão que a transação guarda
    o saldo resultante: a linha se explica sozinha depois, sem depender de
    outra tabela continuar concordando. Mexer só num dos dois deixaria o teste
    medindo um estado que o jogo nunca produz.
    """
    db.session.execute(
        db.update(RodadaCrashCompartilhada)
        .where(RodadaCrashCompartilhada.numero == numero)
        .values(ponto_de_estouro=Decimal(valor))
    )
    compartilhada = (
        db.session.query(RodadaCrashCompartilhada).filter_by(numero=numero).one()
    )
    db.session.execute(
        db.update(RodadaCrash)
        .where(RodadaCrash.compartilhada_id == compartilhada.id)
        .values(ponto_de_estouro=Decimal(valor))
    )
    db.session.commit()
    db.session.expire_all()


# --- a curva e o ciclo ------------------------------------------------------


def test_a_curva_dobra_no_tempo_combinado(app):
    assert multiplicador_no_tempo(0) == Decimal("1.00")
    assert multiplicador_no_tempo(SEGUNDOS_PARA_DOBRAR) == Decimal("2.00")
    assert multiplicador_no_tempo(SEGUNDOS_PARA_DOBRAR * 2) == Decimal("4.00")


def test_o_inverso_da_curva_bate_com_a_curva(app):
    for alvo in ["1.50", "2.00", "5.00", "25.00"]:
        t = segundos_para_multiplicador(Decimal(alvo))
        assert multiplicador_no_tempo(t) == Decimal(alvo)


def test_o_voo_mais_longo_cabe_dentro_do_ciclo(app):
    """Se não coubesse, o avião seria cortado no ar ao bater no teto.

    É a amarra entre a curva e o relógio: acelerar a curva sem refazer esta
    conta, ou apertar o ciclo, quebra aqui em vez de quebrar na tela de
    alguém que estava ganhando 25×.
    """
    assert Decimal(VOO_MAXIMO) >= segundos_para_multiplicador(TETO_DO_MULTIPLICADOR)
    assert DURACAO_DO_CICLO > JANELA_DE_APOSTA + VOO_MAXIMO


def test_o_numero_da_rodada_e_o_relogio(app):
    """Duas telas que nunca se falaram concordam porque as duas olham a hora."""
    assert numero_da_rodada(0) == 0
    assert numero_da_rodada(DURACAO_DO_CICLO - 1) == 0
    assert numero_da_rodada(DURACAO_DO_CICLO) == 1
    assert numero_da_rodada(DURACAO_DO_CICLO * 7 + 3) == 7


def test_a_janela_de_aposta_abre_e_fecha_na_hora(app):
    inicio = DURACAO_DO_CICLO * 5
    assert da_para_apostar(5, inicio)
    assert da_para_apostar(5, inicio + JANELA_DE_APOSTA - 1)
    assert not da_para_apostar(5, inicio + JANELA_DE_APOSTA)
    assert not da_para_apostar(5, inicio + DURACAO_DO_CICLO - 1)


# --- o estouro derivado -----------------------------------------------------


def test_o_estouro_e_o_mesmo_toda_vez(app):
    """Determinístico: é o que permite vinte celulares sem estado no servidor."""
    fator = fator_de(Decimal("2.00"))
    primeiro = ponto_de_estouro_da_rodada("segredo", 12345, fator)
    for _ in range(5):
        assert ponto_de_estouro_da_rodada("segredo", 12345, fator) == primeiro


def test_rodadas_diferentes_dao_estouros_diferentes(app):
    fator = fator_de(Decimal("2.00"))
    valores = {
        ponto_de_estouro_da_rodada("segredo", n, fator) for n in range(200)
    }
    assert len(valores) > 100, "a derivação está colapsando em poucos valores"


def test_segredos_diferentes_dao_estouros_diferentes(app):
    """Sem isto, trocar o segredo não trocaria nada e ele seria decoração."""
    fator = fator_de(Decimal("2.00"))
    de_um = [ponto_de_estouro_da_rodada("um", n, fator) for n in range(50)]
    de_outro = [ponto_de_estouro_da_rodada("outro", n, fator) for n in range(50)]
    assert de_um != de_outro


def test_o_uniforme_fica_dentro_do_intervalo(app):
    """``u`` em (0, 1]: zero dividiria por zero, e acima de 1 não é uniforme."""
    for n in range(500):
        u = uniforme_da_rodada("segredo", n)
        assert 0 < u <= 1


def test_a_derivacao_respeita_a_vantagem_no_valor_esperado(app):
    """A propriedade que faz o jogo ser honesto: ``E[retorno] = 1 - vantagem``.

    Vale para **qualquer** alvo, e é isso que impede existir alvo esperto. O
    teste confere sobre os estouros derivados, e não sobre o sorteio puro:
    trocar o gerador por um HMAC poderia ter estragado a distribuição, e é
    exatamente esse o risco que aqui se cobre.
    """
    vantagem = Decimal("2.00")
    fator = fator_de(vantagem)
    amostra = [
        ponto_de_estouro_da_rodada("segredo", n, fator) for n in range(4000)
    ]
    for alvo in [Decimal("1.50"), Decimal("2.00"), Decimal("5.00")]:
        ganhos = sum(1 for m in amostra if m >= alvo)
        retorno = Decimal(ganhos) / Decimal(len(amostra)) * alvo
        # Folga larga de propósito: 4.000 amostras não fixam a terceira casa,
        # e o que se está checando é que não há viés grosseiro.
        assert abs(retorno - fator) < Decimal("0.08"), (alvo, retorno)


def test_a_derivacao_nunca_passa_do_teto_nem_cai_abaixo_de_um(app):
    fator = fator_de(Decimal("-10.00"))
    for n in range(2000):
        m = ponto_de_estouro_da_rodada("segredo", n, fator)
        assert Decimal("1.00") <= m <= TETO_DO_MULTIPLICADOR


def test_a_derivacao_usa_a_mesma_formula_do_sorteio(app):
    """Uma regra, uma implementação: não há segunda fórmula ao lado.

    Se alguém escrever uma distribuição própria para a rodada derivada, a
    vantagem da casa deixa de ser a que está no painel — e ninguém percebe,
    porque os dois caminhos continuam devolvendo números plausíveis.
    """

    class Fixo:
        def random(self):
            return uniforme_da_rodada("segredo", 99)

    fator = fator_de(Decimal("2.00"))
    assert ponto_de_estouro_da_rodada("segredo", 99, fator) == sortear_ponto_de_estouro(
        fator, Fixo()
    )


# --- a rodada compartilhada -------------------------------------------------


def test_todo_mundo_ve_a_mesma_rodada(app, bc, cassino, relogio):
    """O ponto do jogo: uma curva, vinte telas."""
    relogio.na_janela()
    uma = rodada_compartilhada_de_agora()
    db.session.commit()
    outra = rodada_compartilhada_de_agora()
    assert uma.id == outra.id
    assert uma.ponto_de_estouro == outra.ponto_de_estouro


def test_criar_a_mesma_rodada_duas_vezes_nao_duplica(app, bc, cassino, relogio):
    relogio.na_janela()
    rodada_compartilhada(relogio.numero)
    db.session.commit()
    rodada_compartilhada(relogio.numero)
    db.session.commit()
    assert (
        db.session.query(RodadaCrashCompartilhada)
        .filter_by(numero=relogio.numero)
        .count()
        == 1
    )


def test_a_rodada_congela_a_vantagem(app, bc, cassino, relogio):
    """O dono mexendo no painel não muda onde um avião em voo explode.

    Mesmo princípio da vantagem congelada na aposta dos outros jogos. Aqui
    pesa mais: a vantagem entra na derivação do estouro, então sem o
    congelamento o número mudaria retroativamente no meio do voo.
    """
    relogio.na_janela()
    rodada = rodada_compartilhada_de_agora()
    db.session.commit()
    estouro, vantagem = rodada.ponto_de_estouro, rodada.vantagem

    definir_vantagem("crash", "9.00", cassino["dono"])
    db.session.commit()
    db.session.expire_all()

    de_novo = rodada_compartilhada_de_agora()
    assert de_novo.vantagem == vantagem
    assert de_novo.ponto_de_estouro == estouro


def test_o_segredo_nasce_sozinho_e_nao_muda(app, bc, cassino):
    """Ele precisa sobreviver a reinício: dois workers têm de concordar."""
    primeiro = segredo_do_crash()
    db.session.commit()
    assert primeiro
    assert len(primeiro) >= 32
    assert segredo_do_crash() == primeiro


# --- a aposta ---------------------------------------------------------------


def test_a_aposta_sai_na_hora_e_e_um_lancamento(app, bc, cassino, relogio):
    relogio.na_janela()
    antes = conservacao()
    saldo = cassino["ana"].saldo

    rodada = criar_rodada_crash(cassino["ana"], "10.00", "2.00")
    db.session.commit()

    db.session.expire_all()
    assert cassino["ana"].saldo == saldo - Decimal("10.00")
    assert rodada.transacao_aposta_id is not None
    lancamento = db.session.get(Transacao, rodada.transacao_aposta_id)
    assert lancamento.valor == Decimal("10.00")
    assert conservacao() == antes


def test_fora_da_janela_a_aposta_e_recusada(app, bc, cassino, relogio):
    """Depois que o avião levanta o estouro já foi entregue para as telas.

    Apostar a essa altura seria apostar sabendo o resultado — é o mesmo
    buraco que o gate do reveal fecha, visto do outro lado.
    """
    relogio.no_voo(1)
    antes = conservacao()

    with pytest.raises(ValorInvalido):
        criar_rodada_crash(cassino["ana"], "10.00", "2.00")
    db.session.rollback()

    assert conservacao() == antes


def test_uma_aposta_por_pessoa_por_rodada(app, bc, cassino, relogio):
    relogio.na_janela()
    criar_rodada_crash(cassino["ana"], "10.00", "2.00")
    db.session.commit()

    with pytest.raises(RodadaEmAndamento):
        criar_rodada_crash(cassino["ana"], "10.00", "3.00")
    db.session.rollback()


def test_duas_pessoas_apostam_na_mesma_rodada(app, bc, cassino, relogio):
    relogio.na_janela()
    de_ana = criar_rodada_crash(cassino["ana"], "10.00", "2.00")
    de_bia = criar_rodada_crash(cassino["bia"], "5.00", "4.00")
    db.session.commit()

    assert de_ana.compartilhada_id == de_bia.compartilhada_id
    assert de_ana.ponto_de_estouro == de_bia.ponto_de_estouro


def test_conta_de_sistema_nao_joga(app, bc, cassino, relogio):
    relogio.na_janela()
    with pytest.raises(ValorInvalido):
        criar_rodada_crash(cassino["casa"], "10.00", "2.00")
    db.session.rollback()


def test_alvo_invalido_e_recusado(app, bc, cassino, relogio):
    relogio.na_janela()
    for ruim in ["1.00", "0.50", "26.00", "abc"]:
        with pytest.raises(ValorInvalido):
            criar_rodada_crash(cassino["ana"], "1.00", ruim)
        db.session.rollback()


def test_alvo_no_limite_e_aceito(app):
    assert validar_alvo(str(ALVO_MINIMO)) == ALVO_MINIMO
    assert validar_alvo(str(TETO_DO_MULTIPLICADOR)) == TETO_DO_MULTIPLICADOR


# --- a banca ----------------------------------------------------------------


def test_as_apostas_da_rodada_somam_na_exposicao(app, bc, cassino, relogio):
    """A rodada compartilhada enche a banca sozinha, e isso é de propósito.

    Cada aposta continua sendo uma linha ativa, então vinte apostas na mesma
    janela reservam vinte prêmios máximos. É o que impede a casa de prometer,
    numa rodada só, mais do que ela tem — que é justamente o risco que a
    rodada compartilhada cria e a individual não criava.
    """
    relogio.na_janela()
    assert exposicao_comprometida() == Decimal("0.00")

    criar_rodada_crash(cassino["ana"], "10.00", "2.00")
    db.session.commit()
    uma = exposicao_comprometida()
    assert uma == Decimal("10.00") * TETO_DO_MULTIPLICADOR

    criar_rodada_crash(cassino["bia"], "4.00", "2.00")
    db.session.commit()
    assert exposicao_comprometida() == uma + Decimal("4.00") * TETO_DO_MULTIPLICADOR


def test_a_exposicao_derruba_o_limite_da_aposta_seguinte(app, bc, cassino, relogio):
    relogio.na_janela()
    antes = limite_de_aposta()
    criar_rodada_crash(cassino["ana"], str(antes), "2.00")
    db.session.commit()
    assert limite_de_aposta() < antes


def test_aposta_acima_do_teto_de_banca_e_recusada(app, bc, cassino, relogio, nova_pessoa):
    relogio.na_janela()
    rico = nova_pessoa(nome="rico", saldo="900.00")
    db.session.commit()
    antes = conservacao()

    maximo = limite_de_aposta()
    with pytest.raises(ApostaAlta):
        criar_rodada_crash(rico, str(maximo + Decimal("0.01")), "2.00")
    db.session.rollback()

    assert conservacao() == antes


def test_a_recusa_diz_o_numero_e_para(app, bc, cassino, relogio, nova_pessoa):
    """Número seco. Quem está apostando quer saber quanto cabe, não um texto."""
    relogio.na_janela()
    rico = nova_pessoa(nome="rico", saldo="900.00")
    db.session.commit()
    maximo = limite_de_aposta()

    with pytest.raises(ApostaAlta) as erro:
        criar_rodada_crash(rico, str(maximo + Decimal("0.01")), "2.00")
    db.session.rollback()

    assert str(erro.value) == f"Aposta máxima agora: {maximo} VVC"


def test_a_banca_e_medida_com_a_casa_travada(app, bc, cassino, relogio, monkeypatch):
    """Duas apostas simultâneas liam o mesmo caixa e passavam as duas.

    ``criar_rodada_crash`` travava a linha do JOGADOR mas media a banca com a
    casa solta: entre ler a exposição e gravar a aposta cabia outra requisição
    inteira, e as duas se achavam dentro do limite. A correção é travar a casa
    na leitura que **decide** — e só nela, porque tomar lock em todo GET faria
    as telas esperarem umas pelas outras à toa.

    O teste espia o parâmetro em vez do SQL porque no SQLite o ``FOR UPDATE``
    nem é emitido (o banco serializa a escrita sozinho): o que dá para
    garantir aqui é o contrato entre as duas funções, e é ele que alguém
    quebraria sem querer numa limpeza futura.
    """
    from vavacoin import caladinho

    visto = []
    verdadeiro = caladinho.limite_de_aposta

    def espiao(sessao=None, travar=False):
        visto.append(travar)
        return verdadeiro(sessao, travar=travar)

    monkeypatch.setattr(caladinho, "limite_de_aposta", espiao)

    relogio.na_janela()
    criar_rodada_crash(cassino["ana"], "1.00", "2.00")
    db.session.commit()

    assert visto == [True], "a banca foi medida com a casa destravada"


def test_os_quatro_jogos_medem_a_banca_pela_mesma_regra(app, bc, cassino, relogio):
    """Uma regra, um lugar. Quatro cópias é como uma delas fica para trás."""
    from vavacoin import caladinho
    import inspect as _inspect

    for nome in [
        "criar_rodada",
        "criar_rodada_crash",
        "criar_rodada_torre",
        "jogar_dados",
    ]:
        fonte = _inspect.getsource(getattr(caladinho, nome))
        assert "exigir_que_caiba_na_banca" in fonte, nome


# --- o segredo do estouro ---------------------------------------------------


def test_o_estouro_nao_sai_durante_a_janela_de_aposta(app, bc, cassino, relogio):
    """O ponto de segurança da feature, no nível do jogo.

    Quem souber onde a rodada explode enquanto ainda dá para apostar aposta
    só quando é favorável, e ganha sempre.
    """
    relogio.na_janela()
    rodada = rodada_compartilhada_de_agora()
    db.session.commit()
    assert rodada.ponto_de_estouro > 0  # existe no banco

    visao = visao_do_ciclo()
    assert visao["estouro"] is None
    assert visao["da_para_apostar"] is True


def test_o_estouro_sai_depois_que_a_janela_fecha(app, bc, cassino, relogio):
    relogio.na_janela()
    rodada = rodada_compartilhada_de_agora()
    db.session.commit()
    esperado = rodada.ponto_de_estouro

    relogio.no_voo(0)
    visao = visao_do_ciclo()
    assert visao["estouro"] == esperado
    assert visao["da_para_apostar"] is False


def test_a_rota_nao_entrega_o_estouro_durante_a_janela(app, bc, cassino, relogio):
    """Pela rota direta, que é o caminho que a tela não mostra.

    Não basta a página não imprimir o número: quem quisesse trapacear iria
    direto no endereço do JSON.
    """
    relogio.na_janela()
    rodada = rodada_compartilhada_de_agora()
    db.session.commit()
    segredo = str(rodada.ponto_de_estouro)

    resposta = _entrar(app, "ana").get("/caladinho/crash/ciclo")
    corpo = resposta.get_data(as_text=True)

    assert resposta.status_code == 200
    assert resposta.get_json()["estouro"] is None
    assert segredo not in corpo, "o ponto de estouro vazou pela rota do ciclo"


def test_a_tela_nao_entrega_o_estouro_durante_a_janela(app, bc, cassino, relogio):
    relogio.na_janela()
    rodada = rodada_compartilhada_de_agora()
    db.session.commit()
    segredo = str(rodada.ponto_de_estouro)

    corpo = _entrar(app, "ana").get("/caladinho/crash").get_data(as_text=True)

    assert segredo not in corpo, "o ponto de estouro vazou pela página"


def test_nao_da_para_perguntar_pela_rodada_seguinte(app, bc, cassino, relogio):
    """A metade da tranca que costuma ser esquecida.

    Esconder o estouro da rodada de agora não adianta nada se der para pedir
    o da próxima: bastaria olhar antes de apostar. A rota não aceita número
    nenhum, e mandar um na query string não muda a resposta.
    """
    relogio.na_janela()
    atual = rodada_compartilhada_de_agora()
    db.session.commit()
    seguinte = rodada_compartilhada(relogio.numero + 1)
    db.session.commit()
    proibido = str(seguinte.ponto_de_estouro)

    cliente = _entrar(app, "ana")
    for endereco in [
        "/caladinho/crash/ciclo",
        f"/caladinho/crash/ciclo?numero={relogio.numero + 1}",
        f"/caladinho/crash/ciclo?rodada={relogio.numero + 1}",
        f"/caladinho/crash/ciclo?n={relogio.numero + 5}",
    ]:
        dados = cliente.get(endereco).get_json()
        assert dados["numero"] == atual.numero, endereco
        assert dados["estouro"] is None, endereco
        assert proibido not in cliente.get(endereco).get_data(as_text=True), endereco


def test_o_historico_nao_inclui_rodada_que_ainda_nao_voou(app, bc, cassino, relogio):
    """A fileira de resultados é outro lugar por onde o número escaparia."""
    relogio.na_janela()
    rodada = rodada_compartilhada_de_agora()
    db.session.commit()

    assert all(r.numero != rodada.numero for r in ultimas_rodadas())

    relogio.depois_do_voo()
    assert any(r.numero == rodada.numero for r in ultimas_rodadas())


def test_a_aposta_da_pessoa_nao_entrega_o_estouro_enquanto_voa(
    app, bc, cassino, relogio
):
    relogio.na_janela()
    rodada = criar_rodada_crash(cassino["ana"], "10.00", "2.00")
    db.session.commit()

    visao = visao_da_rodada_crash(rodada)
    assert visao["ponto_de_estouro"] is None


def test_a_aposta_congela_o_mesmo_estouro_da_rodada(app, bc, cassino, relogio):
    """A cópia na aposta e o número da rodada não podem divergir.

    A aposta guarda o estouro para se explicar sozinha depois, como a
    transação guarda o saldo resultante. Cópia é dívida: se um dia as duas
    deixarem de concordar, o jogador vê um número e é pago por outro.
    """
    relogio.na_janela()
    rodada = criar_rodada_crash(cassino["ana"], "10.00", "2.00")
    db.session.commit()

    compartilhada = db.session.get(RodadaCrashCompartilhada, rodada.compartilhada_id)
    assert rodada.ponto_de_estouro == compartilhada.ponto_de_estouro
    assert rodada.vantagem == compartilhada.vantagem


# --- a liquidação -----------------------------------------------------------


def test_a_aposta_nao_resolve_antes_do_voo_acabar(app, bc, cassino, relogio):
    relogio.na_janela()
    rodada = criar_rodada_crash(cassino["ana"], "10.00", "2.00")
    db.session.commit()
    _forcar_estouro(relogio.numero, "5.00")

    relogio.no_voo(0)
    liquidar_crash_vencido()
    db.session.commit()

    db.session.expire_all()
    assert db.session.get(RodadaCrash, rodada.id).estado == RodadaCrash.ATIVA


def test_alvo_abaixo_do_estouro_ganha_no_alvo(app, bc, cassino, relogio):
    relogio.na_janela()
    antes = conservacao()
    rodada = criar_rodada_crash(cassino["ana"], "10.00", "2.00")
    db.session.commit()
    _forcar_estouro(relogio.numero, "5.00")

    relogio.depois_do_voo()
    liquidar_crash_vencido()
    db.session.commit()

    db.session.expire_all()
    fechada = db.session.get(RodadaCrash, rodada.id)
    assert fechada.estado == RodadaCrash.RETIRADA
    assert fechada.multiplicador == Decimal("2.00")
    assert fechada.premio == Decimal("20.00")
    assert conservacao() == antes


def test_alvo_acima_do_estouro_perde(app, bc, cassino, relogio):
    relogio.na_janela()
    antes = conservacao()
    saldo = cassino["ana"].saldo
    rodada = criar_rodada_crash(cassino["ana"], "10.00", "8.00")
    db.session.commit()
    _forcar_estouro(relogio.numero, "3.00")

    relogio.depois_do_voo()
    liquidar_crash_vencido()
    db.session.commit()

    db.session.expire_all()
    fechada = db.session.get(RodadaCrash, rodada.id)
    assert fechada.estado == RodadaCrash.ESTOURADA
    assert fechada.premio == Decimal("0.00")
    # O multiplicador guardado é ONDE estourou: é o que a tela mostra.
    assert fechada.multiplicador == Decimal("3.00")
    assert db.session.get(type(cassino["ana"]), cassino["ana"].id).saldo == saldo - Decimal("10.00")
    assert conservacao() == antes


def test_liquidar_duas_vezes_paga_uma_vez_so(app, bc, cassino, relogio):
    """Vinte telas liquidam a mesma rodada no mesmo instante. Uma paga."""
    relogio.na_janela()
    antes = conservacao()
    rodada = criar_rodada_crash(cassino["ana"], "10.00", "2.00")
    db.session.commit()
    _forcar_estouro(relogio.numero, "5.00")

    relogio.depois_do_voo()
    liquidar_crash_vencido()
    db.session.commit()
    saldo = db.session.get(type(cassino["ana"]), cassino["ana"].id).saldo
    premios = db.session.query(Transacao).filter_by(tipo="premio_crash").count()

    liquidar_crash_vencido()
    db.session.commit()

    db.session.expire_all()
    assert db.session.get(type(cassino["ana"]), cassino["ana"].id).saldo == saldo
    assert db.session.query(Transacao).filter_by(tipo="premio_crash").count() == premios
    assert conservacao() == antes


def test_a_liquidacao_fecha_a_aposta_de_quem_nao_abriu_a_tela(
    app, bc, cassino, relogio
):
    """Quem fechou a aba não pode prender o caixa da casa para sempre.

    É o mesmo serviço que a expiração da torre presta, e aqui ele é mais
    necessário: a rodada é de todos, e quem lê a tela é só uma pessoa.
    """
    relogio.na_janela()
    da_bia = criar_rodada_crash(cassino["bia"], "10.00", "2.00")
    db.session.commit()
    _forcar_estouro(relogio.numero, "5.00")
    assert exposicao_comprometida() > 0

    relogio.depois_do_voo()
    # Quem "abriu a tela" foi a Ana, que nem apostou.
    liquidar_crash_vencido()
    db.session.commit()

    db.session.expire_all()
    assert db.session.get(RodadaCrash, da_bia.id).encerrada
    assert exposicao_comprometida() == Decimal("0.00")


def test_resolver_por_jogador_faz_o_mesmo(app, bc, cassino, relogio):
    """O interruptor do jogo chama por jogador; o desfecho tem de ser um só."""
    relogio.na_janela()
    rodada = criar_rodada_crash(cassino["ana"], "10.00", "2.00")
    db.session.commit()
    _forcar_estouro(relogio.numero, "5.00")

    relogio.depois_do_voo()
    resolver_crash(cassino["ana"])
    db.session.commit()

    db.session.expire_all()
    assert db.session.get(RodadaCrash, rodada.id).premio == Decimal("20.00")


def test_desligar_o_crash_nao_prende_o_caixa(app, bc, cassino, relogio):
    """Fechar a rota com aposta em pé prenderia a exposição para sempre."""
    relogio.na_janela()
    antes = conservacao()
    criar_rodada_crash(cassino["ana"], "10.00", "2.00")
    db.session.commit()
    assert exposicao_comprometida() > 0

    definir_ligado("crash", False, cassino["dono"])
    db.session.commit()

    db.session.expire_all()
    assert exposicao_comprometida() == Decimal("0.00")
    assert conservacao() == antes


# --- não existe saque manual ------------------------------------------------


def test_nao_existe_rota_de_sacar(app, bc, cassino, relogio):
    """Sem clique, conhecer a curva deixa de ser dinheiro de graça.

    Com botão, quem vê que estoura em 3× clica em 2,99× e nunca mais perde —
    e o cliente PRECISA conhecer a curva para desenhar a explosão na hora
    certa. O jogo só fecha sem o botão, e este teste é o que impede alguém de
    o trazer de volta sem refazer a conta.
    """
    relogio.na_janela()
    criar_rodada_crash(cassino["ana"], "10.00", "2.00")
    db.session.commit()

    cliente = _entrar(app, "ana")
    assert cliente.post("/caladinho/crash/sacar").status_code == 404

    import vavacoin.caladinho as caladinho

    assert not hasattr(caladinho, "sacar_crash")


# --- a web ------------------------------------------------------------------


def test_a_tela_do_crash_abre(app, bc, cassino, relogio):
    relogio.na_janela()
    resposta = _entrar(app, "ana").get("/caladinho/crash")
    assert resposta.status_code == 200
    assert "Crash" in resposta.get_data(as_text=True)


def test_o_lobby_leva_ao_crash(app, bc, cassino):
    """O crash voltou ao ar: o link tem de estar lá de novo."""
    corpo = _entrar(app, "ana").get("/caladinho/").get_data(as_text=True)
    assert "/caladinho/crash" in corpo


def test_apostar_e_ganhar_pela_web(app, bc, cassino, relogio):
    relogio.na_janela()
    antes = conservacao()
    cliente = _entrar(app, "ana")

    cliente.post(
        "/caladinho/crash/apostar",
        data={"aposta": "10.00", "alvo": "2.00"},
        follow_redirects=True,
    )
    db.session.expire_all()
    rodada = db.session.query(RodadaCrash).order_by(RodadaCrash.id.desc()).first()
    assert rodada is not None
    _forcar_estouro(relogio.numero, "5.00")

    relogio.depois_do_voo()
    corpo = cliente.get("/caladinho/crash").get_data(as_text=True)

    db.session.expire_all()
    assert db.session.get(RodadaCrash, rodada.id).premio == Decimal("20.00")
    assert "20.00" in corpo
    assert conservacao() == antes


def test_sem_script_o_formulario_vem_fechado_fora_da_janela(
    app, bc, cassino, relogio
):
    """Degradar com honestidade: nada de botão que parece clicável e não faz.

    Sem JavaScript é o servidor quem decide se o formulário abre, e ele decide
    pelo relógio de quando desenhou a página. O "Atualizar" é a saída para a
    janela seguinte.
    """
    cliente = _entrar(app, "ana")

    relogio.na_janela()
    aberto = cliente.get("/caladinho/crash").get_data(as_text=True)
    assert 'id="crash-campos"' in aberto
    assert "disabled" not in aberto.split('id="crash-campos"')[1].split(">")[0]

    relogio.no_voo(1)
    fechado = cliente.get("/caladinho/crash").get_data(as_text=True)
    assert "disabled" in fechado.split('id="crash-campos"')[1].split(">")[0]
    assert "Atualizar" in fechado


def test_a_tela_nao_tem_estilo_nem_script_embutido(app, bc, cassino, relogio):
    """A CSP é `script-src 'self'` e `style-src 'self'`: nada inline passa."""
    relogio.na_janela()
    corpo = _entrar(app, "ana").get("/caladinho/crash").get_data(as_text=True)
    assert "style=" not in corpo
    assert "onclick=" not in corpo
    assert "<script>" not in corpo


def test_o_historico_compartilhado_aparece_na_tela(app, bc, cassino, relogio):
    """A fileira que todo mundo vê igual é metade da graça do jogo."""
    relogio.na_janela()
    rodada_compartilhada_de_agora()
    db.session.commit()
    _forcar_estouro(relogio.numero, "7.00")

    relogio.proxima_rodada()
    corpo = _entrar(app, "ana").get("/caladinho/crash").get_data(as_text=True)
    assert "7.00×" in corpo


def test_o_ciclo_manda_o_relogio_do_servidor(app, bc, cassino, relogio):
    """O relógio do celular pode estar minutos fora; o que vale é o de cá."""
    relogio.na_janela()
    dados = _entrar(app, "ana").get("/caladinho/crash/ciclo").get_json()
    assert dados["agora"] == int(relogio.epoch * 1000)
    assert dados["numero"] == relogio.numero
    assert dados["voa_em"] == (relogio.numero * DURACAO_DO_CICLO + JANELA_DE_APOSTA) * 1000


def test_as_minhas_aparecem_no_historico(app, bc, cassino, relogio):
    relogio.na_janela()
    criar_rodada_crash(cassino["ana"], "10.00", "2.00")
    db.session.commit()
    _forcar_estouro(relogio.numero, "5.00")
    relogio.depois_do_voo()
    liquidar_crash_vencido()
    db.session.commit()

    assert len(historico_crash(cassino["ana"])) == 1
