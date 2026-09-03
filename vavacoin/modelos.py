"""Modelos do núcleo monetário.

Três tabelas e nada mais nesta fatia: quem tem dinheiro (``Usuario``), o que
autoriza uma pessoa a entrar (``Convite``) e o registro imutável de todo
centavo que se moveu (``Transacao``).
"""

from datetime import datetime, timezone
from decimal import Decimal

import bcrypt
from flask import current_app, has_app_context
from flask_login import UserMixin
from sqlalchemy import CheckConstraint

from .constantes import USUARIO_BANCO_CENTRAL
from .dinheiro import ZERO, Dinheiro
from .extensoes import db
from .nomes import normalizar_nome


def _custo_bcrypt():
    """Custo do bcrypt, configurável só para baixar o tempo da suíte.

    Em produção fica no padrão da biblioteca. Baixar o custo em teste é a
    diferença entre a suíte rodar em segundos ou em minutos — e teste lento
    é teste que ninguém roda.
    """
    if has_app_context():
        return current_app.config.get("BCRYPT_ROUNDS", 12)
    return 12


def agora():
    """Instante atual em UTC, com fuso explícito.

    Datetime ingênuo em ledger é dívida: seis meses depois ninguém sabe se
    aquele horário era de Brasília ou do servidor.
    """
    return datetime.now(timezone.utc)


class Usuario(db.Model, UserMixin):
    """Uma conta com saldo. O Banco Central é uma delas.

    O Banco Central é um ``Usuario`` de propósito: assim ele participa do
    mesmo ``mover()``, do mesmo ledger e da mesma soma de conservação que
    todo mundo. Se ele fosse um caso especial fora da tabela, a verificação
    de massa teria uma exceção — e exceção em invariante é onde massa some.
    """

    __tablename__ = "usuario"

    id = db.Column(db.Integer, primary_key=True)
    #: Como a pessoa escreveu — com maiúscula e acento, se quis. É o que
    #: aparece na tela.
    nome_usuario = db.Column(db.String(50), nullable=False, index=True)
    #: A forma que o sistema compara: sem acento, minúscula. É esta que é
    #: única, e é por ela que se busca no login e na transferência. Sem isso,
    #: "João" e "joao" seriam duas contas.
    nome_normalizado = db.Column(db.String(50), unique=True, nullable=False, index=True)
    nome_exibicao = db.Column(db.String(80), nullable=False)
    senha_hash = db.Column(db.String(128), nullable=True)
    eh_banco_central = db.Column(db.Boolean, nullable=False, default=False)
    #: Quando a conta foi encerrada pelo Banco Central. Anulável: a conta viva
    #: é a que tem isto em branco. Encerrada não autentica e não recebe
    #: transferência, mas **continua existindo** — as linhas do ledger que
    #: falam dela precisam de alguém para apontar, senão a auditoria passa a
    #: acusar para sempre.
    encerrada_em = db.Column(db.DateTime(timezone=True), nullable=True)
    #: A conta da casa do Caladinho. É uma conta de verdade no ledger, com
    #: saldo próprio — não uma coluna de configuração. Separada das duas
    #: coisas com que não pode se confundir: não é o Banco Central (que
    #: emite) nem a conta pessoal do dono (que joga). Nasce sem senha, então
    #: não entra pelo site.
    eh_cassino = db.Column(db.Boolean, nullable=False, default=False)
    #: O saldo desta pessoa aparece para os outros?
    #:
    #: **Nasce ligado** e a pessoa desliga no perfil — é opt-out, por decisão
    #: do dono. (O ``CLAUDE.md`` descreve o ranking antigo como opt-in; esta é
    #: uma decisão nova dele, não uma contradição a resolver no código.)
    #:
    #: Uma preferência só, e não uma por tela: quem decide se um saldo aparece
    #: é sempre :meth:`saldo_visivel_para`. Regra por tela é como duas telas
    #: começam a discordar sobre a mesma escolha da pessoa.
    saldo_publico = db.Column(db.Boolean, nullable=False, default=True)
    #: O cofre de um reino. Conta de verdade no ledger, com saldo próprio — e
    #: conta de sistema: não joga, não é renomeada e **não entra pela tela**.
    #: Quem opera o reino é uma pessoa com o papel de operador; o cofre é só
    #: onde o dinheiro mora.
    eh_cofre = db.Column(db.Boolean, nullable=False, default=False)
    #: A conta-sombra que ficou no lugar de alguém que o Banco Central apagou
    #: de verdade. Não é gente e nunca foi: nasce sem senha, com saldo zero e
    #: já encerrada, e existe por um motivo só — dar às linhas do ledger
    #: alguém para apontar.
    #:
    #: **Por que uma por remoção, e não uma compartilhada.** A ideia natural é
    #: uma única "conta removida" para todas as exclusões. Ela quebra no
    #: ``CHECK`` ``origem_id <> destino_id``: se duas pessoas que já
    #: transferiram entre si forem apagadas, a linha das duas viraria
    #: sombra → sombra e o banco recusaria. Uma sombra por remoção nunca
    #: colide, porque a sombra nasce depois de todas as linhas que vai
    #: receber. Na tela todas se chamam "conta removida" — a diferença é só
    #: de identidade no banco.
    eh_removida = db.Column(db.Boolean, nullable=False, default=False)
    #: De quem é esta conta, quando ela é de uma casa de jogo. Anulável de
    #: propósito: "sem dono" é um estado, não um caso especial — e é por aqui
    #: que uma transferência de posse entra depois, sem reescrever nada.
    dono_id = db.Column(db.Integer, db.ForeignKey("usuario.id"), nullable=True)
    #: Desde quando. O lucro do dono é somado do ledger a partir desta data,
    #: e não guardado num contador — contador diverge e ninguém percebe.
    dono_desde = db.Column(db.DateTime(timezone=True), nullable=True)
    saldo = db.Column(Dinheiro, nullable=False, default=ZERO)
    criado_em = db.Column(db.DateTime(timezone=True), nullable=False, default=agora)

    __table_args__ = (
        # Saldo negativo não é um estado possível desta economia. Se o banco
        # aceitar um, o bug já passou por aqui — a checagem é a última rede.
        CheckConstraint("saldo >= 0", name="ck_usuario_saldo_nao_negativo"),
        # Havia aqui um CHECK proibindo senha no Banco Central. Ele caiu na
        # migration 353a30f6e6f5: a decisão é que o BC entra pelo site e tem
        # god mode. O que protege a conta agora não é a porta fechada — é a
        # senha (por CLI, com hash), o freio de tentativas, e o fato de tudo
        # que ele faz passar pelo ledger com ator e motivo.
    )

    dono = db.relationship("Usuario", remote_side=[id], foreign_keys=[dono_id])

    def definir_nome(self, nome_usuario):
        """Guarda as duas formas de uma vez.

        Existe para que ninguém escreva ``nome_usuario`` sem escrever o
        normalizado junto — os dois andam em par, e um sem o outro deixa a
        conta invisível para o login.
        """
        self.nome_usuario = (nome_usuario or "").strip()
        self.nome_normalizado = normalizar_nome(self.nome_usuario)
        if not self.nome_normalizado:
            raise ValueError("nome de usuário vazio depois de normalizado")
        return self.nome_normalizado

    def definir_senha(self, senha):
        """Guarda o hash bcrypt da senha. Texto puro nunca toca o banco.

        O Banco Central também tem senha, e ela é definida por CLI
        (``flask senha-bc``) — nunca no código, nunca em migration. Quem
        entrar nela é dono de tudo: é a decisão registrada no CLAUDE.md, e o
        que resta ao código é não piorá-la (hash bcrypt, freio de tentativas,
        e todo movimento no ledger com o ator anotado).
        """
        self.senha_hash = bcrypt.hashpw(
            senha.encode("utf-8"), bcrypt.gensalt(_custo_bcrypt())
        ).decode("utf-8")

    def verificar_senha(self, senha):
        """Confere a senha. Conta sem hash (Banco Central) nunca autentica."""
        if not self.senha_hash:
            return False
        return bcrypt.checkpw(senha.encode("utf-8"), self.senha_hash.encode("utf-8"))

    @property
    def encerrada(self):
        return self.encerrada_em is not None

    def saldo_visivel_para(self, quem):
        """O saldo desta conta aparece para ``quem``?

        **A regra única.** Todo lugar que mostra saldo de outra pessoa passa
        por aqui — se um dia uma tela decidir sozinha, é aqui que ela deveria
        ter perguntado.

        Três casos, nesta ordem:

        - a própria pessoa vê o próprio saldo, sempre;
        - o Banco Central vê tudo, porque audita tudo e isso já está
          registrado como o poder dele;
        - qualquer outro só vê se a pessoa deixou público.

        Fora do login não vê nada: sem alguém autenticado não há "quem".
        """
        if quem is None or not getattr(quem, "is_authenticated", False):
            return False
        if getattr(quem, "id", None) == self.id:
            return True
        if quem.eh_admin:
            return True
        return self.saldo_publico

    @property
    def is_active(self):
        """Flask-Login: toda conta com senha entra, o Banco Central inclusive.

        Uma conta ainda sem senha definida não entra — é o estado do Banco
        Central logo depois da gênese, antes do ``flask senha-bc``.

        Conta encerrada também não entra. É aqui que o encerramento vira
        realidade para o login: ``login_user`` recusa quem não é ``is_active``,
        então não há uma segunda checagem para alguém esquecer.
        """
        return self.senha_hash is not None and not self.encerrada

    @property
    def eh_admin(self):
        """Quem tem god mode. Hoje, só o Banco Central."""
        return self.eh_banco_central

    @property
    def eh_conta_de_sistema(self):
        """Banco Central, casa do cassino e cofre de reino: não são jogadores.

        Nenhum resgata convite nem recebe transferência de gente. O dinheiro
        chega neles por caminho próprio — ajuste, aposta, imposto. E nenhum
        entra pela tela: é o que impede "quem sabe a senha do cofre é rei".
        """
        return (
            self.eh_banco_central
            or self.eh_cassino
            or self.eh_cofre
            or self.eh_removida
        )

    def __repr__(self):
        return f"<Usuario {self.nome_usuario} saldo={self.saldo}>"


class Convite(db.Model):
    """O direito de uma pessoa entrar na economia.

    O convite é da **pessoa**, não da conta, e só serve uma vez. Ele já
    carregou um saque de 50 VVC; hoje carrega só o acesso, e o dinheiro chega
    depois por transferência ou por ajuste do Banco Central.
    """

    __tablename__ = "convite"

    id = db.Column(db.Integer, primary_key=True)
    codigo = db.Column(db.String(32), unique=True, nullable=False, index=True)
    #: Para quem o código foi entregue. Serve para auditoria humana ("esse é
    #: o do Fulano"), não para autenticação.
    destinatario = db.Column(db.String(80), nullable=True)
    #: Conta que resgatou. UNIQUE garante, no banco, que uma conta não resgata
    #: dois códigos, mesmo que dois pedidos cheguem juntos.
    usuario_id = db.Column(
        db.Integer, db.ForeignKey("usuario.id"), unique=True, nullable=True
    )
    resgatado_em = db.Column(db.DateTime(timezone=True), nullable=True)
    criado_em = db.Column(db.DateTime(timezone=True), nullable=False, default=agora)

    usuario = db.relationship("Usuario", backref="convite")

    @property
    def resgatado(self):
        return self.usuario_id is not None

    def __repr__(self):
        estado = "resgatado" if self.resgatado else "livre"
        return f"<Convite {self.codigo} {estado}>"


class Transacao(db.Model):
    """Uma linha do ledger. Só o ``mover()`` escreve aqui.

    Guarda os saldos resultantes dos dois lados porque auditar "onde o
    dinheiro estava na terça" reconstruindo somas do zero é caro e sujeito a
    erro; com o saldo pós-movimento gravado, cada linha se explica sozinha.
    """

    __tablename__ = "transacao"

    id = db.Column(db.Integer, primary_key=True)
    #: NULL quando o dinheiro entrou no mundo: a gênese, ou uma emissão feita
    #: pelo administrador ao ajustar saldo para cima. Somar estas linhas dá o
    #: supply — ver ``moeda.supply_emitido()``.
    origem_id = db.Column(db.Integer, db.ForeignKey("usuario.id"), nullable=True, index=True)
    #: NULL quando o dinheiro saiu do mundo: uma queima, feita pelo Banco
    #: Central ao baixar o próprio saldo. É o simétrico da emissão, e é o que
    #: permite o supply descer — sem ela o teto seria catraca de uma via.
    destino_id = db.Column(db.Integer, db.ForeignKey("usuario.id"), nullable=True, index=True)
    valor = db.Column(Dinheiro, nullable=False)
    tipo = db.Column(db.String(30), nullable=False, index=True)
    motivo = db.Column(db.String(200), nullable=True)
    #: Quem mandou fazer, quando não é o dono da origem — o administrador
    #: ajustando o saldo de alguém. É o que responde "por que meu saldo
    #: mudou?" seis meses depois.
    ator_id = db.Column(db.Integer, db.ForeignKey("usuario.id"), nullable=True, index=True)
    saldo_origem_depois = db.Column(Dinheiro, nullable=True)
    saldo_destino_depois = db.Column(Dinheiro, nullable=True)
    criado_em = db.Column(db.DateTime(timezone=True), nullable=False, default=agora, index=True)

    __table_args__ = (
        CheckConstraint("valor > 0", name="ck_transacao_valor_positivo"),
        CheckConstraint(
            "origem_id IS NULL OR destino_id IS NULL OR origem_id <> destino_id",
            name="ck_transacao_origem_diferente_destino",
        ),
        # Uma linha sem os dois lados não seria movimento nenhum.
        CheckConstraint(
            "origem_id IS NOT NULL OR destino_id IS NOT NULL",
            name="ck_transacao_tem_algum_lado",
        ),
        # Origem ausente é criação de dinheiro. A lista de tipos que podem
        # fazer isso é curta e mora também no banco: qualquer outro tipo sem
        # origem seria moeda aparecendo sem ninguém ter decidido cunhar.
        CheckConstraint(
            "(origem_id IS NOT NULL) OR tipo IN ('genese', 'emissao')",
            name="ck_transacao_sem_origem_so_emite",
        ),
        # Destino ausente é destruição de dinheiro. Mesma disciplina, do
        # outro lado: só a queima pode sumir com moeda.
        CheckConstraint(
            "(destino_id IS NOT NULL) OR tipo = 'queima'",
            name="ck_transacao_sem_destino_so_queima",
        ),
    )

    origem = db.relationship("Usuario", foreign_keys=[origem_id])
    destino = db.relationship("Usuario", foreign_keys=[destino_id])
    ator = db.relationship("Usuario", foreign_keys=[ator_id])

    def __repr__(self):
        return f"<Transacao {self.tipo} {self.valor} -> {self.destino_id}>"


class RegistroAdministrativo(db.Model):
    """Diário do god mode: o que o administrador fez, quando e por quê.

    O ledger já explica todo centavo, mas metade do poder do administrador
    não mexe em dinheiro — emitir convite, criar conta, olhar o extrato de
    alguém. Sem este diário, essas ações não deixariam rastro nenhum.

    Não substitui o ledger e não é fonte de verdade sobre saldo: é o registro
    de decisão, para responder "quem fez isso e por quê" depois.
    """

    __tablename__ = "registro_administrativo"

    id = db.Column(db.Integer, primary_key=True)
    ator_id = db.Column(
        db.Integer, db.ForeignKey("usuario.id"), nullable=False, index=True
    )
    #: Verbo curto e estável, para dar para filtrar: "convite", "conta",
    #: "ajuste", "reset", "extrato".
    acao = db.Column(db.String(40), nullable=False, index=True)
    #: Sobre quem ou sobre o quê. Guardado como texto, não como FK, para o
    #: registro sobreviver ao sumiço do alvo.
    alvo = db.Column(db.String(80), nullable=True)
    #: O que aconteceu, em números ("de 50.00 para 80.00").
    detalhe = db.Column(db.String(300), nullable=True)
    #: Por quê, escrito pelo administrador. Obrigatório onde mexe em dinheiro.
    motivo = db.Column(db.String(300), nullable=True)
    criado_em = db.Column(
        db.DateTime(timezone=True), nullable=False, default=agora, index=True
    )

    ator = db.relationship("Usuario", foreign_keys=[ator_id])

    def __repr__(self):
        return f"<RegistroAdministrativo {self.acao} {self.alvo}>"


def registrar_acao(ator, acao, alvo=None, detalhe=None, motivo=None, sessao=None):
    """Anota uma ação do god mode. Não faz ``commit``."""
    sessao = sessao or db.session
    registro = RegistroAdministrativo(
        ator_id=ator.id if isinstance(ator, Usuario) else ator,
        acao=acao,
        alvo=alvo,
        detalhe=detalhe,
        motivo=motivo,
    )
    sessao.add(registro)
    sessao.flush()
    return registro


class Configuracao(db.Model):
    """Interruptores do jogo, guardados no banco e não no código.

    Mesma ideia do ``pagina_<x>_visivel`` do Benbals: o que o dono liga e
    desliga durante o jogo é dado, não constante — senão trocar de ideia
    exige deploy.
    """

    __tablename__ = "configuracao"

    id = db.Column(db.Integer, primary_key=True)
    chave = db.Column(db.String(60), unique=True, nullable=False, index=True)
    valor = db.Column(db.String(200), nullable=False)
    atualizado_em = db.Column(
        db.DateTime(timezone=True), nullable=False, default=agora, onupdate=agora
    )

    def __repr__(self):
        return f"<Configuracao {self.chave}={self.valor}>"


#: Se o saldo da casa aparece para os jogadores.
CHAVE_CAIXA_VISIVEL = "caladinho_caixa_visivel"

#: Se o ranking geral aparece. **Nasce ligado** — é o que o dono quer usar
#: agora. Como as outras visibilidades, desligar esconde o link E fecha a
#: rota.
CHAVE_RANKING_VISIVEL = "ranking_visivel"

#: Se a página dos reinos aparece para a turma. Mesmo interruptor das outras
#: visibilidades: o Banco Central liga quando o RPG começar.
CHAVE_REINOS_VISIVEIS = "reinos_visiveis"

#: Se dá para criar conta sem código de convite. **Nasce ligado**, por decisão
#: do dono: como quem entra começa com saldo zero, o convite deixou de ser o
#: que segura a porta. O sistema de convite continua inteiro — link, painel,
#: uso único —, e desligar este interruptor volta a exigi-lo.
CHAVE_CADASTRO_ABERTO = "cadastro_aberto"


def config_texto(chave, padrao=None, sessao=None):
    """O valor cru de uma configuração, ou ``padrao`` se ela nunca foi gravada.

    ``config_ligada`` é o caso booleano desta função. Existe separada porque
    nem toda configuração é interruptor: a vantagem da casa, por exemplo, é um
    número que o dono escolhe dentro de uma faixa.
    """
    sessao = sessao or db.session
    registro = sessao.execute(
        db.select(Configuracao).where(Configuracao.chave == chave)
    ).scalar_one_or_none()
    if registro is None:
        return padrao
    return registro.valor


def definir_config_texto(chave, valor, sessao=None):
    """Grava o valor cru. Não faz ``commit``."""
    sessao = sessao or db.session
    registro = sessao.execute(
        db.select(Configuracao).where(Configuracao.chave == chave)
    ).scalar_one_or_none()
    if registro is None:
        registro = Configuracao(chave=chave, valor=str(valor))
        sessao.add(registro)
    else:
        registro.valor = str(valor)
        registro.atualizado_em = agora()
    sessao.flush()
    return registro


def config_ligada(chave, padrao=False, sessao=None):
    """A configuração está ligada? Guardada como "1"/"0"."""
    sessao = sessao or db.session
    registro = sessao.execute(
        db.select(Configuracao).where(Configuracao.chave == chave)
    ).scalar_one_or_none()
    if registro is None:
        return padrao
    return registro.valor == "1"


def definir_config(chave, ligada, sessao=None):
    """Grava a configuração. Não faz ``commit``."""
    sessao = sessao or db.session
    valor = "1" if ligada else "0"
    registro = sessao.execute(
        db.select(Configuracao).where(Configuracao.chave == chave)
    ).scalar_one_or_none()
    if registro is None:
        registro = Configuracao(chave=chave, valor=valor)
        sessao.add(registro)
    else:
        registro.valor = valor
        registro.atualizado_em = agora()
    sessao.flush()
    return registro


class RodadaMines(db.Model):
    """Uma rodada de mines. É a fonte da verdade do servidor.

    O tabuleiro (``minas``) mora só aqui e **nunca vai para o cliente
    enquanto a rodada está ativa** — é o coração do anti-trapaça. O navegador
    informa qual casa foi clicada; quem decide se era mina é o servidor.

    Estados:

    - ``ativa``: em andamento; dá para revelar casa ou retirar;
    - ``estourada``: revelou uma mina e perdeu a aposta (já debitada);
    - ``retirada``: parou e recebeu o prêmio.

    "No máximo uma rodada ativa por jogador" tem duas defesas: o ``FOR
    UPDATE`` mais a checagem na criação, e um **índice único parcial** no
    banco. A segunda existe porque a primeira não cobre duas requisições que
    chegam ao mesmo tempo em processos diferentes.
    """

    __tablename__ = "rodada_mines"

    ATIVA = "ativa"
    ESTOURADA = "estourada"
    RETIRADA = "retirada"

    id = db.Column(db.Integer, primary_key=True)
    jogador_id = db.Column(
        db.Integer, db.ForeignKey("usuario.id"), nullable=False, index=True
    )
    aposta = db.Column(Dinheiro, nullable=False)
    #: De qual reino era o jogador **no instante da aposta**. Congelado aqui,
    #: e nulo quando ele não era de reino nenhum — nulo é o "não cidadão", e é
    #: um valor legítimo, não ausência de dado.
    #:
    #: Congelado porque o acordo é o cassino pagar 10% do lucro tirado dos
    #: cidadãos daquele reino, e calcular isso depois, consultando a cidadania
    #: atual, faria alguém entrando ou saindo reescrever imposto de rodada
    #: passada. A conta do mês mudaria sozinha, que é exatamente o que gera
    #: briga entre o dono e o amigo. Mesmo princípio da vantagem congelada.
    reino_id = db.Column(
        db.Integer, db.ForeignKey("reino.id"), nullable=True, index=True
    )
    minas_escolhidas = db.Column(db.Integer, nullable=False)

    #: Posições das minas (0..24), em CSV. SEGREDO DO SERVIDOR enquanto ativa.
    minas = db.Column(db.String(120), nullable=False)
    #: Casas seguras já abertas, em CSV.
    reveladas = db.Column(db.String(120), nullable=False, default="")

    estado = db.Column(db.String(12), nullable=False, default=ATIVA, index=True)
    #: A casa que estourou. Serve para a tela marcar a mina em que a pessoa
    #: pisou, diferente das outras — e para responder "qual casa eu cliquei?"
    #: se alguém contestar a rodada depois.
    casa_estourada = db.Column(db.Integer, nullable=True)
    #: A vantagem da casa **no instante da aposta**, em pontos percentuais
    #: (``2.00`` = 2%). Congelada aqui de propósito: o dono pode mudar a
    #: vantagem a qualquer momento, e rodada aberta não pode ser afetada —
    #: senão daria para baixar o pagamento com a pessoa no meio do jogo, que
    #: é exatamente a acusação que o cassino não pode receber. Guardada em
    #: ``Dinheiro`` porque é o tipo exato que o projeto já tem (inteiro de
    #: centésimos); aqui o "centavo" é um centésimo de ponto percentual.
    vantagem = db.Column(Dinheiro, nullable=False, default=Decimal("2.00"))
    #: Multiplicador acumulado, sem teto. Dois decimais exatos, como dinheiro.
    multiplicador = db.Column(Dinheiro, nullable=False, default=ZERO)
    premio = db.Column(Dinheiro, nullable=False, default=ZERO)

    #: As duas pontas no ledger. Toda rodada tem aposta; só a retirada tem
    #: prêmio.
    transacao_aposta_id = db.Column(
        db.Integer, db.ForeignKey("transacao.id"), nullable=True
    )
    transacao_premio_id = db.Column(
        db.Integer, db.ForeignKey("transacao.id"), nullable=True
    )

    criada_em = db.Column(
        db.DateTime(timezone=True), nullable=False, default=agora, index=True
    )
    #: Quando a pessoa mexeu pela última vez. É por aqui que a rodada
    #: abandonada expira, em vez de prender o caixa da casa para sempre —
    #: mesma coluna e mesma razão da rodada de torre.
    mexida_em = db.Column(
        db.DateTime(timezone=True), nullable=False, default=agora, index=True
    )
    encerrada_em = db.Column(db.DateTime(timezone=True), nullable=True)

    jogador = db.relationship("Usuario", foreign_keys=[jogador_id])

    __table_args__ = (
        CheckConstraint("aposta > 0", name="ck_rodada_aposta_positiva"),
        CheckConstraint(
            "minas_escolhidas >= 1 AND minas_escolhidas <= 24",
            name="ck_rodada_minas",
        ),
        CheckConstraint(
            "estado IN ('ativa', 'estourada', 'retirada')", name="ck_rodada_estado"
        ),
        # Índice único parcial: o banco recusa a segunda rodada ativa do mesmo
        # jogador, mesmo em corrida entre processos.
        db.Index(
            "uq_uma_rodada_ativa_por_jogador",
            "jogador_id",
            unique=True,
            sqlite_where=db.text("estado = 'ativa'"),
            postgresql_where=db.text("estado = 'ativa'"),
        ),
    )

    @staticmethod
    def _lista(texto):
        return [int(c) for c in (texto or "").split(",") if c != ""]

    @property
    def casas_reveladas(self):
        return self._lista(self.reveladas)

    @property
    def casas_com_mina(self):
        return self._lista(self.minas)

    @property
    def casas_abertas(self):
        """Quantas casas a pessoa abriu, contando a mina em que pisou.

        ``reveladas`` guarda só as seguras, porque é delas que sai o
        multiplicador. Para o histórico isso mentia: quem estourou no primeiro
        clique lia "0 abertas" e concluía que a rodada tinha encerrado sozinha.
        """
        return len(self.casas_reveladas) + (0 if self.casa_estourada is None else 1)

    @property
    def encerrada(self):
        return self.estado != self.ATIVA

    def __repr__(self):
        return f"<RodadaMines {self.id} {self.estado} aposta={self.aposta}>"


class RodadaCrash(db.Model):
    """Uma rodada de crash. Como a do mines, é a fonte da verdade do servidor.

    **O resultado já está decidido quando a linha nasce.** O ponto de estouro
    é sorteado no instante da aposta, o alvo é declarado junto, e ``alvo <=
    ponto_de_estouro`` responde sozinho se a rodada foi ganha. O tempo só
    decide *quando* isso é aplicado, e o saque manual só decide se a pessoa
    saiu por um número menor que o alvo.

    Por isso resolver uma rodada esquecida na leitura da página não é
    re-sortear nada: a decisão é de antes, e aplicar tarde dá o mesmo
    resultado que aplicar na hora.

    ``ponto_de_estouro`` é SEGREDO DO SERVIDOR enquanto a rodada vive — é o
    equivalente ao tabuleiro do mines, e o ponto em que um descuido entregaria
    o jogo.

    Estados: ``ativa``, ``estourada`` (o alvo passou do estouro) e
    ``retirada`` (saiu no alvo ou antes dele).
    """

    __tablename__ = "rodada_crash"

    ATIVA = "ativa"
    ESTOURADA = "estourada"
    RETIRADA = "retirada"

    id = db.Column(db.Integer, primary_key=True)
    jogador_id = db.Column(
        db.Integer, db.ForeignKey("usuario.id"), nullable=False, index=True
    )
    aposta = db.Column(Dinheiro, nullable=False)
    #: De qual reino era o jogador **no instante da aposta**. Congelado aqui,
    #: e nulo quando ele não era de reino nenhum — nulo é o "não cidadão", e é
    #: um valor legítimo, não ausência de dado.
    #:
    #: Congelado porque o acordo é o cassino pagar 10% do lucro tirado dos
    #: cidadãos daquele reino, e calcular isso depois, consultando a cidadania
    #: atual, faria alguém entrando ou saindo reescrever imposto de rodada
    #: passada. A conta do mês mudaria sozinha, que é exatamente o que gera
    #: briga entre o dono e o amigo. Mesmo princípio da vantagem congelada.
    reino_id = db.Column(
        db.Integer, db.ForeignKey("reino.id"), nullable=True, index=True
    )
    #: A vantagem da casa no instante da aposta, em pontos percentuais. Mesma
    #: disciplina da rodada de mines: o dono muda a vantagem quando quiser, e
    #: rodada aberta não sente.
    vantagem = db.Column(Dinheiro, nullable=False, default=Decimal("2.00"))
    #: Onde a curva para. SEGREDO DO SERVIDOR enquanto ``ativa``.
    ponto_de_estouro = db.Column(Dinheiro, nullable=False)
    #: Onde o jogador declarou que quer sair. Resolvido pelo servidor sem
    #: depender de clique — é o que zera o risco de rede.
    alvo = db.Column(Dinheiro, nullable=False)

    estado = db.Column(db.String(12), nullable=False, default=ATIVA, index=True)
    #: Por onde a rodada saiu de fato: o alvo, o número do saque manual, ou o
    #: ponto de estouro quando perdeu.
    multiplicador = db.Column(Dinheiro, nullable=False, default=ZERO)
    premio = db.Column(Dinheiro, nullable=False, default=ZERO)

    transacao_aposta_id = db.Column(
        db.Integer, db.ForeignKey("transacao.id"), nullable=True
    )
    transacao_premio_id = db.Column(
        db.Integer, db.ForeignKey("transacao.id"), nullable=True
    )

    #: O instante zero da curva. É contra ele, e contra o relógio do servidor,
    #: que o saque manual é validado.
    iniciada_em = db.Column(
        db.DateTime(timezone=True), nullable=False, default=agora, index=True
    )
    encerrada_em = db.Column(db.DateTime(timezone=True), nullable=True)

    jogador = db.relationship("Usuario", foreign_keys=[jogador_id])

    __table_args__ = (
        CheckConstraint("aposta > 0", name="ck_crash_aposta_positiva"),
        CheckConstraint("alvo > 100", name="ck_crash_alvo_acima_de_um"),
        CheckConstraint("ponto_de_estouro >= 100", name="ck_crash_estouro_minimo"),
        CheckConstraint(
            "estado IN ('ativa', 'estourada', 'retirada')", name="ck_crash_estado"
        ),
        # Mesma defesa do mines: o banco recusa a segunda rodada ativa do
        # mesmo jogador, mesmo em corrida entre processos.
        db.Index(
            "uq_uma_rodada_crash_ativa_por_jogador",
            "jogador_id",
            unique=True,
            sqlite_where=db.text("estado = 'ativa'"),
            postgresql_where=db.text("estado = 'ativa'"),
        ),
    )

    @property
    def encerrada(self):
        return self.estado != self.ATIVA

    @property
    def ganhou(self):
        """Decidido na aposta: o alvo cabe dentro do estouro?"""
        return self.alvo <= self.ponto_de_estouro

    def __repr__(self):
        return f"<RodadaCrash {self.id} {self.estado} aposta={self.aposta}>"


class RodadaTorre(db.Model):
    """Uma rodada de torre. Fonte da verdade do servidor.

    **A torre inteira é sorteada na aposta**, como o tabuleiro do mines:
    ``armadilhas`` guarda a porta armadilhada de cada andar, em CSV, e é
    SEGREDO DO SERVIDOR enquanto a rodada vive. Sortear andar a andar não
    daria nenhuma garantia a mais e abriria a porta para o sorteio depender de
    quando a requisição chega — a mesma classe de erro que o tabuleiro em
    branco nos custou.

    ``escolhas`` guarda a porta que a pessoa abriu em cada andar, na ordem.
    O tamanho dela É o andar em que a pessoa está.

    Estados: ``ativa``, ``estourada`` (abriu a armadilha) e ``retirada``
    (parou e recebeu, ou bateu o teto, ou a rodada expirou).

    "No máximo uma rodada ativa por jogador" tem as duas defesas de sempre: o
    ``FOR UPDATE`` mais a checagem na criação, e um índice único parcial no
    banco para a corrida entre processos.
    """

    __tablename__ = "rodada_torre"

    ATIVA = "ativa"
    ESTOURADA = "estourada"
    RETIRADA = "retirada"

    id = db.Column(db.Integer, primary_key=True)
    jogador_id = db.Column(
        db.Integer, db.ForeignKey("usuario.id"), nullable=False, index=True
    )
    aposta = db.Column(Dinheiro, nullable=False)
    #: De qual reino era o jogador **no instante da aposta**. Congelado aqui,
    #: e nulo quando ele não era de reino nenhum — nulo é o "não cidadão", e é
    #: um valor legítimo, não ausência de dado.
    #:
    #: Congelado porque o acordo é o cassino pagar 10% do lucro tirado dos
    #: cidadãos daquele reino, e calcular isso depois, consultando a cidadania
    #: atual, faria alguém entrando ou saindo reescrever imposto de rodada
    #: passada. A conta do mês mudaria sozinha, que é exatamente o que gera
    #: briga entre o dono e o amigo. Mesmo princípio da vantagem congelada.
    reino_id = db.Column(
        db.Integer, db.ForeignKey("reino.id"), nullable=True, index=True
    )
    #: A vantagem da casa no instante da aposta, em pontos percentuais.
    #: Congelada pela mesma razão dos outros jogos: rodada aberta não muda de
    #: tabela no meio, nem para melhor nem para pior.
    vantagem = db.Column(Dinheiro, nullable=False, default=Decimal("2.00"))
    #: Quantas portas por andar. É a dificuldade escolhida pelo jogador.
    portas = db.Column(db.Integer, nullable=False)

    #: A porta armadilhada de cada andar (0..portas-1), em CSV, do térreo ao
    #: topo. SEGREDO DO SERVIDOR enquanto ``ativa``.
    armadilhas = db.Column(db.String(200), nullable=False)
    #: As portas abertas pela pessoa, na ordem. O tamanho é o andar atual.
    escolhas = db.Column(db.String(200), nullable=False, default="")

    estado = db.Column(db.String(12), nullable=False, default=ATIVA, index=True)
    #: Em que andar a pessoa pisou na armadilha. Serve para a tela marcar o
    #: andar que estourou e para responder "onde foi que eu errei?" depois.
    andar_estourado = db.Column(db.Integer, nullable=True)
    multiplicador = db.Column(Dinheiro, nullable=False, default=ZERO)
    premio = db.Column(Dinheiro, nullable=False, default=ZERO)

    transacao_aposta_id = db.Column(
        db.Integer, db.ForeignKey("transacao.id"), nullable=True
    )
    transacao_premio_id = db.Column(
        db.Integer, db.ForeignKey("transacao.id"), nullable=True
    )

    criada_em = db.Column(
        db.DateTime(timezone=True), nullable=False, default=agora, index=True
    )
    #: Quando a pessoa mexeu pela última vez. É por aqui que a rodada
    #: abandonada expira, em vez de prender o caixa da casa para sempre.
    mexida_em = db.Column(
        db.DateTime(timezone=True), nullable=False, default=agora, index=True
    )
    encerrada_em = db.Column(db.DateTime(timezone=True), nullable=True)

    jogador = db.relationship("Usuario", foreign_keys=[jogador_id])

    __table_args__ = (
        CheckConstraint("aposta > 0", name="ck_torre_aposta_positiva"),
        CheckConstraint("portas >= 2 AND portas <= 4", name="ck_torre_portas"),
        CheckConstraint(
            "estado IN ('ativa', 'estourada', 'retirada')", name="ck_torre_estado"
        ),
        db.Index(
            "uq_uma_rodada_torre_ativa_por_jogador",
            "jogador_id",
            unique=True,
            sqlite_where=db.text("estado = 'ativa'"),
            postgresql_where=db.text("estado = 'ativa'"),
        ),
    )

    @staticmethod
    def _lista(texto):
        if not texto:
            return []
        return [int(p) for p in texto.split(",") if p != ""]

    @property
    def armadilha_por_andar(self):
        return self._lista(self.armadilhas)

    @property
    def portas_abertas(self):
        return self._lista(self.escolhas)

    @property
    def andares_subidos(self):
        """Quantos andares a pessoa venceu.

        Na rodada estourada o último andar aberto é justamente o que a
        derrubou, e ele não conta como subido.
        """
        subidos = len(self.portas_abertas)
        return subidos - 1 if self.andar_estourado is not None else subidos

    @property
    def encerrada(self):
        return self.estado != self.ATIVA

    def __repr__(self):
        return f"<RodadaTorre {self.id} {self.estado} aposta={self.aposta}>"


class RodadaDados(db.Model):
    """Uma rodada de dados. Nasce **já resolvida**.

    É o único jogo do Caladinho sem estado intermediário: a rolagem, o
    resultado e os dois lançamentos acontecem dentro da mesma transação do
    POST da aposta. Não existe rodada ativa, e por isso não existe rodada
    abandonada prendendo o caixa da casa nem liquidação para acontecer depois.

    A linha continua sendo gravada — e não só o lançamento no ledger — porque
    é ela que responde "que número saiu?" quando alguém contestar, e porque é
    dela que a tela relê o resultado depois de um refresh. A lição do
    tabuleiro em branco vale aqui igual: resultado que só vive no redirect
    some quando a rede falha.
    """

    __tablename__ = "rodada_dados"

    GANHA = "ganha"
    PERDIDA = "perdida"

    id = db.Column(db.Integer, primary_key=True)
    jogador_id = db.Column(
        db.Integer, db.ForeignKey("usuario.id"), nullable=False, index=True
    )
    aposta = db.Column(Dinheiro, nullable=False)
    #: De qual reino era o jogador **no instante da aposta**. Congelado aqui,
    #: e nulo quando ele não era de reino nenhum — nulo é o "não cidadão", e é
    #: um valor legítimo, não ausência de dado.
    #:
    #: Congelado porque o acordo é o cassino pagar 10% do lucro tirado dos
    #: cidadãos daquele reino, e calcular isso depois, consultando a cidadania
    #: atual, faria alguém entrando ou saindo reescrever imposto de rodada
    #: passada. A conta do mês mudaria sozinha, que é exatamente o que gera
    #: briga entre o dono e o amigo. Mesmo princípio da vantagem congelada.
    reino_id = db.Column(
        db.Integer, db.ForeignKey("reino.id"), nullable=True, index=True
    )
    #: A vantagem no instante da aposta, em pontos percentuais. Aqui ela não
    #: precisa proteger rodada aberta — não existe rodada aberta —, mas fica
    #: gravada porque é o que explica o multiplicador desta linha seis meses
    #: depois, quando a vantagem vigente já for outra.
    vantagem = db.Column(Dinheiro, nullable=False, default=Decimal("2.00"))

    #: "menor" ou "maior".
    sentido = db.Column(db.String(6), nullable=False)
    alvo = db.Column(db.Integer, nullable=False)
    #: O que o servidor rolou, de 1 a 100.
    resultado = db.Column(db.Integer, nullable=False)

    estado = db.Column(db.String(8), nullable=False, index=True)
    multiplicador = db.Column(Dinheiro, nullable=False, default=ZERO)
    premio = db.Column(Dinheiro, nullable=False, default=ZERO)

    transacao_aposta_id = db.Column(
        db.Integer, db.ForeignKey("transacao.id"), nullable=True
    )
    transacao_premio_id = db.Column(
        db.Integer, db.ForeignKey("transacao.id"), nullable=True
    )

    criada_em = db.Column(
        db.DateTime(timezone=True), nullable=False, default=agora, index=True
    )

    jogador = db.relationship("Usuario", foreign_keys=[jogador_id])

    __table_args__ = (
        CheckConstraint("aposta > 0", name="ck_dados_aposta_positiva"),
        CheckConstraint("alvo >= 1 AND alvo <= 99", name="ck_dados_alvo"),
        CheckConstraint(
            "resultado >= 1 AND resultado <= 100", name="ck_dados_resultado"
        ),
        CheckConstraint("sentido IN ('menor', 'maior')", name="ck_dados_sentido"),
        CheckConstraint("estado IN ('ganha', 'perdida')", name="ck_dados_estado"),
    )

    @property
    def ganhou(self):
        return self.estado == self.GANHA

    @property
    def encerrada(self):
        """Sempre. Existe para a tela tratar os quatro jogos do mesmo jeito."""
        return True

    def __repr__(self):
        return f"<RodadaDados {self.id} {self.estado} aposta={self.aposta}>"


class Reino(db.Model):
    """Um reino: um cofre, cidadãos que pediram para entrar, e um operador.

    Genérico desde o primeiro dia. "Alfheim" é o nome de uma linha desta
    tabela, não uma constante no código — vão existir outros reinos, e o
    segundo não pode exigir reescrever o primeiro.

    **O poder é do reino, não da pessoa.** Quem opera é uma conta pessoal com
    o papel de operador *deste* reino (:class:`OperadorDoReino`); tirar o papel
    tira o poder. O cofre é só onde o dinheiro mora, e **não autentica pela
    tela** — se a maneira de exercer o poder fosse entrar na conta do cofre,
    quem soubesse a senha seria rei e o ledger diria "o cofre cobrou", nunca
    quem digitou. É o bug das contas de tesouraria do Benbals, e ele não entra
    aqui.
    """

    __tablename__ = "reino"

    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(60), nullable=False)
    #: Forma comparável do nome, como em ``Usuario``: é esta que é única.
    nome_normalizado = db.Column(db.String(60), unique=True, nullable=False, index=True)
    #: A conta onde o dinheiro do reino mora. Conta de verdade no ledger, com
    #: saldo próprio, para que o cofre participe da mesma conservação de massa
    #: que todo mundo.
    cofre_id = db.Column(
        db.Integer, db.ForeignKey("usuario.id"), unique=True, nullable=False
    )
    #: Quanto do lucro que o cassino tira dos cidadãos DESTE reino vira
    #: imposto, em pontos percentuais. O combinado de hoje é 10%, mas ele é
    #: valor inicial de uma coluna, não constante no código: cada reino
    #: negocia o seu, e mudar fica registrado.
    aliquota_cassino = db.Column(Dinheiro, nullable=False, default=Decimal("10.00"))
    #: Prejuízo acumulado que ainda vai abater lucro futuro. Cresce quando o
    #: cassino perde com os cidadãos deste reino num período liquidado, e é
    #: consumido quando um período seguinte dá lucro.
    #:
    #: Abate o **lucro tributável**, não o imposto: abater 100 de lucro com
    #: alíquota de 10% reduz o imposto em 10, não em 100.
    abatimento = db.Column(Dinheiro, nullable=False, default=ZERO)
    #: Juros por dia sobre dívida em aberto, em pontos percentuais.
    #: Editável pelo operador dentro de uma faixa, com registro — mesmo
    #: desenho da vantagem do cassino.
    juros_diarios = db.Column(Dinheiro, nullable=False, default=Decimal("1.00"))
    criado_em = db.Column(db.DateTime(timezone=True), nullable=False, default=agora)

    cofre = db.relationship("Usuario", foreign_keys=[cofre_id])

    def __repr__(self):
        return f"<Reino {self.nome}>"


class Cidadania(db.Model):
    """Alguém pediu para entrar num reino — e pode pedir para sair.

    **Opt-in com saída**, que é o princípio que rege o projeto inteiro:
    ninguém vira cidadão sem pedir, e ninguém fica preso.

    A linha não é apagada na saída: ``saiu_em`` é carimbado. Assim a pergunta
    "esta pessoa era cidadã quando aquela cobrança aconteceu?" continua tendo
    resposta depois, que é o que uma cobrança contestada precisa.

    **Sair não apaga dívida.** A dívida é uma relação entre quem cobrou e quem
    deve, não um atributo da cidadania — por isso ela não mora aqui, e por
    isso sair não a toca em nada.

    **Um reino por pessoa**, por decisão do dono, e a garantia é do banco: o
    índice único parcial é por ``usuario_id`` onde ``saiu_em IS NULL``, sem o
    ``reino_id``. Duas requisições simultâneas pedindo entrada em dois reinos
    diferentes não passam as duas — o que a rota confere, o banco impede.

    O modelo em si continua genérico: são N reinos, e a exclusividade é UM
    índice. Se um dia a dupla cidadania for liberada, é esse índice que ganha
    o ``reino_id`` de volta, e nada mais muda. A restrição está num lugar só,
    de propósito.
    """

    __tablename__ = "cidadania"

    id = db.Column(db.Integer, primary_key=True)
    reino_id = db.Column(
        db.Integer, db.ForeignKey("reino.id"), nullable=False, index=True
    )
    usuario_id = db.Column(
        db.Integer, db.ForeignKey("usuario.id"), nullable=False, index=True
    )
    entrou_em = db.Column(db.DateTime(timezone=True), nullable=False, default=agora)
    saiu_em = db.Column(db.DateTime(timezone=True), nullable=True)

    reino = db.relationship("Reino", foreign_keys=[reino_id])
    usuario = db.relationship("Usuario", foreign_keys=[usuario_id])

    __table_args__ = (
        # UMA cidadania ativa por pessoa, em qualquer reino. Sem `reino_id`
        # na chave: é isso que faz a exclusividade ser do banco e não da
        # rota. Entrar de novo onde já se está, ou entrar num segundo reino,
        # batem os dois aqui.
        db.Index(
            "uq_uma_cidadania_ativa_por_pessoa",
            "usuario_id",
            unique=True,
            sqlite_where=db.text("saiu_em IS NULL"),
            postgresql_where=db.text("saiu_em IS NULL"),
        ),
    )

    @property
    def ativa(self):
        return self.saiu_em is None


class LiquidacaoDeImposto(db.Model):
    """Um período de imposto do cassino já acertado com um reino.

    A linha é a **guarda de status** do pagamento: ``(reino, início, fim)`` é
    UNIQUE, e ela entra antes de o dinheiro sair. Clicar duas vezes no mesmo
    período bate no índice e nenhum centavo se move — que é o que impede o
    mesmo lucro de ser cobrado de novo.

    Guarda a conta inteira, e não só o valor pago, porque a pergunta que vem
    depois é "por que o imposto desse período foi esse?". Com ``lucro_bruto``,
    ``abatimento_usado``, ``lucro_tributavel`` e ``aliquota`` na linha, a
    resposta está aqui e não em refazer a soma na mão.
    """

    __tablename__ = "liquidacao_de_imposto"

    id = db.Column(db.Integer, primary_key=True)
    reino_id = db.Column(
        db.Integer, db.ForeignKey("reino.id"), nullable=False, index=True
    )
    inicio = db.Column(db.DateTime(timezone=True), nullable=False)
    fim = db.Column(db.DateTime(timezone=True), nullable=False)

    #: Apostas menos prêmios das rodadas atribuídas ao reino no período.
    #: Negativo quando o cassino perdeu para os cidadãos dele.
    lucro_bruto = db.Column(Dinheiro, nullable=False)
    #: Quanto do prejuízo acumulado foi consumido para abater este período.
    abatimento_usado = db.Column(Dinheiro, nullable=False, default=ZERO)
    #: ``max(0, lucro_bruto - abatimento_usado)``. É sobre isto que a alíquota
    #: incide.
    lucro_tributavel = db.Column(Dinheiro, nullable=False, default=ZERO)
    aliquota = db.Column(Dinheiro, nullable=False)
    imposto = db.Column(Dinheiro, nullable=False, default=ZERO)

    #: Quem apertou o botão, e o lançamento que pagou (nulo se o imposto deu
    #: zero — período sem lucro tributável não move dinheiro).
    liquidado_por_id = db.Column(
        db.Integer, db.ForeignKey("usuario.id"), nullable=False
    )
    transacao_id = db.Column(
        db.Integer, db.ForeignKey("transacao.id"), nullable=True
    )
    criada_em = db.Column(db.DateTime(timezone=True), nullable=False, default=agora)

    reino = db.relationship("Reino", foreign_keys=[reino_id])

    __table_args__ = (
        CheckConstraint("fim > inicio", name="ck_liquidacao_periodo"),
        CheckConstraint("imposto >= 0", name="ck_liquidacao_imposto"),
        CheckConstraint("abatimento_usado >= 0", name="ck_liquidacao_abatimento"),
        # Um período por reino, liquidado uma vez só.
        db.UniqueConstraint(
            "reino_id", "inicio", "fim", name="uq_um_periodo_por_reino"
        ),
    )

    def __repr__(self):
        return f"<LiquidacaoDeImposto reino={self.reino_id} imposto={self.imposto}>"


class PedidoDeCidadania(db.Model):
    """Uma cidadania que falta um lado confirmar.

    **Uma tabela para os dois caminhos**, e não duas quase iguais: o reino
    convida e a pessoa aceita, ou a pessoa pede e o operador aprova. Muda quem
    começou (``origem``) e, por consequência, quem confirma — o resto é
    idêntico, e duas tabelas gêmeas acabariam divergindo numa regra só.

    O invariante é o mesmo dos dois lados: **ninguém entra sozinho e ninguém é
    colocado à força**. Sempre são as duas partes, em alguma ordem.

    Quem confirma:

    - ``origem = "reino"`` (convite): confirma a **pessoa**. O reino já falou
      quando enviou.
    - ``origem = "pessoa"`` (pedido): confirma um **operador**. A pessoa já
      falou quando pediu.

    A exclusividade de cidadania é conferida **na confirmação**, nunca no
    envio: entre convidar e aceitar podem passar dias, e o que vale é o
    estado de quem aceita na hora em que aceita. Convidar quem já é cidadão de
    outro reino é legítimo — ela é que decide sair de lá ou recusar.
    """

    __tablename__ = "pedido_de_cidadania"

    REINO = "reino"
    PESSOA = "pessoa"

    PENDENTE = "pendente"
    ACEITO = "aceito"
    RECUSADO = "recusado"

    id = db.Column(db.Integer, primary_key=True)
    reino_id = db.Column(
        db.Integer, db.ForeignKey("reino.id"), nullable=False, index=True
    )
    usuario_id = db.Column(
        db.Integer, db.ForeignKey("usuario.id"), nullable=False, index=True
    )
    #: Qual lado começou. Decide quem pode confirmar.
    origem = db.Column(db.String(8), nullable=False)
    #: Quem apertou o botão de enviar. Do lado do reino é o operador — nunca o
    #: cofre, que não autentica; do lado da pessoa é ela mesma.
    criado_por_id = db.Column(db.Integer, db.ForeignKey("usuario.id"), nullable=False)
    criado_em = db.Column(db.DateTime(timezone=True), nullable=False, default=agora)

    estado = db.Column(db.String(10), nullable=False, default=PENDENTE, index=True)
    respondido_em = db.Column(db.DateTime(timezone=True), nullable=True)
    respondido_por_id = db.Column(
        db.Integer, db.ForeignKey("usuario.id"), nullable=True
    )

    reino = db.relationship("Reino", foreign_keys=[reino_id])
    usuario = db.relationship("Usuario", foreign_keys=[usuario_id])

    __table_args__ = (
        CheckConstraint("origem IN ('reino', 'pessoa')", name="ck_pedido_origem"),
        CheckConstraint(
            "estado IN ('pendente', 'aceito', 'recusado')", name="ck_pedido_estado"
        ),
        # Uma pendência por dupla pessoa/reino. Convidar de novo quem já tem
        # convite aberto não é estado novo, é o mesmo convite — e sem isto a
        # tela do outro lado encheria de linhas idênticas.
        db.Index(
            "uq_uma_pendencia_por_pessoa_no_reino",
            "reino_id",
            "usuario_id",
            unique=True,
            sqlite_where=db.text("estado = 'pendente'"),
            postgresql_where=db.text("estado = 'pendente'"),
        ),
    )

    @property
    def pendente(self):
        return self.estado == self.PENDENTE

    @property
    def eh_convite(self):
        """Partiu do reino, então quem confirma é a pessoa."""
        return self.origem == self.REINO

    def __repr__(self):
        return f"<PedidoDeCidadania {self.id} {self.origem} {self.estado}>"


class OperadorDoReino(db.Model):
    """Quem pode exercer os poderes de um reino.

    Tabela separada, e não uma coluna em ``Reino``, porque o papel é de
    quantas pessoas o reino quiser: dar um ministro a alguém é inserir uma
    linha. O poder continua sendo do reino — perdeu o papel, perdeu o poder.
    """

    __tablename__ = "operador_do_reino"

    id = db.Column(db.Integer, primary_key=True)
    reino_id = db.Column(
        db.Integer, db.ForeignKey("reino.id"), nullable=False, index=True
    )
    usuario_id = db.Column(
        db.Integer, db.ForeignKey("usuario.id"), nullable=False, index=True
    )
    desde = db.Column(db.DateTime(timezone=True), nullable=False, default=agora)

    reino = db.relationship("Reino", foreign_keys=[reino_id])
    usuario = db.relationship("Usuario", foreign_keys=[usuario_id])

    __table_args__ = (
        db.Index(
            "uq_um_papel_por_pessoa_no_reino", "reino_id", "usuario_id", unique=True
        ),
    )


class Cobranca(db.Model):
    """Um lote de cobrança: um clique do operador, N dívidas.

    Existe para o lote ser **idempotente**. O ``token`` é gerado quando a tela
    é desenhada e é único no banco: o segundo POST do mesmo botão bate no
    índice e não cobra ninguém de novo. Guarda também o que foi pedido, para
    a pergunta "de onde saiu esse valor?" ter resposta.
    """

    __tablename__ = "cobranca"

    ABSOLUTA = "absoluta"
    PERCENTUAL = "percentual"

    id = db.Column(db.Integer, primary_key=True)
    reino_id = db.Column(
        db.Integer, db.ForeignKey("reino.id"), nullable=False, index=True
    )
    #: Quem clicou. Nunca o cofre — o ledger registra a pessoa.
    operador_id = db.Column(db.Integer, db.ForeignKey("usuario.id"), nullable=False)
    tipo = db.Column(db.String(12), nullable=False)
    #: VVC por cidadão na absoluta; pontos percentuais na percentual.
    parametro = db.Column(Dinheiro, nullable=False)
    motivo = db.Column(db.String(200), nullable=False)
    #: Chave de idempotência do lote.
    token = db.Column(db.String(64), unique=True, nullable=False)
    criada_em = db.Column(db.DateTime(timezone=True), nullable=False, default=agora)

    __table_args__ = (
        CheckConstraint(
            "tipo IN ('absoluta', 'percentual')", name="ck_cobranca_tipo"
        ),
        CheckConstraint("parametro > 0", name="ck_cobranca_parametro"),
    )


class Distribuicao(db.Model):
    """Um lote de repasse: um clique do operador, N pagamentos.

    Gêmea de :class:`Cobranca`, e existe pela mesma razão — mas aqui a razão
    é mais forte, porque **isto move dinheiro**. Cobrar duas vezes cria
    dívidas repetidas, que o operador apaga perdoando; distribuir duas vezes
    esvazia o cofre, e não há como desfazer.

    O ``token`` é gerado quando a tela é desenhada e é UNIQUE no banco. O
    token da sessão já barra o clique duplo sequencial; este índice barra o
    que a sessão não vê — dois POSTs simultâneos, em processos diferentes,
    lendo o mesmo cookie antes de qualquer um gastá-lo.
    """

    __tablename__ = "distribuicao"

    id = db.Column(db.Integer, primary_key=True)
    reino_id = db.Column(
        db.Integer, db.ForeignKey("reino.id"), nullable=False, index=True
    )
    #: Quem clicou. Nunca o cofre — o ledger registra a pessoa.
    operador_id = db.Column(db.Integer, db.ForeignKey("usuario.id"), nullable=False)
    valor_por_pessoa = db.Column(Dinheiro, nullable=False)
    total = db.Column(Dinheiro, nullable=False)
    quantos = db.Column(db.Integer, nullable=False)
    motivo = db.Column(db.String(200), nullable=False)
    #: Chave de idempotência do lote.
    token = db.Column(db.String(64), unique=True, nullable=False)
    criada_em = db.Column(db.DateTime(timezone=True), nullable=False, default=agora)

    __table_args__ = (
        CheckConstraint("valor_por_pessoa > 0", name="ck_distribuicao_valor"),
        CheckConstraint("quantos > 0", name="ck_distribuicao_quantos"),
    )


class Divida(db.Model):
    """O que uma pessoa deve a um reino.

    **Cobrar não tira dinheiro de ninguém**: cria esta linha. Pagar é ato do
    devedor, e é o que move VVC. É a decisão do dono, e é o que mantém a
    coerência com "nada acontece com uma pessoa sem que ela tenha pedido".

    ## A taxa congela na criação

    Cada dívida carrega ``juros_diarios``, a taxa que valia quando ela nasceu.
    Mudar a taxa do reino depois **não reprecifica cobrança antiga** — mesmo
    princípio da vantagem congelada na aposta do cassino, e pelo mesmo motivo:
    senão dá para encarecer retroativamente a dívida de alguém.

    ## Negociar e perdoar

    Quem criou a dívida pode fixar o **valor de quitação** em qualquer ponto
    entre o principal (juros zerados) e o total com os juros corridos até ali,
    e pode simplesmente perdoar, apagando a linha.

    Nem desconto nem perdão movem dinheiro: **dívida nunca foi dinheiro no
    ledger**, é uma cobrança pendente. Só o pagamento move, por ``mover()``.
    Por isso perdoar pode apagar a linha sem deixar a auditoria acusando —
    não há lançamento nenhum apontando para ela.

    ``quitacao`` é o total a pagar **acumulado**, incluindo o que já foi pago;
    guardá-lo assim deixa ``pago`` como o único contador que anda, e
    ``restante`` como uma subtração. Com ele preenchido os juros param: o
    credor fixou um número, e número fixado não cresce.

    ## Como os juros são contados

    Sem tarefa agendada — o plano do PythonAnywhere dá uma por dia e ela não
    vai ser gasta nisso. Os juros saem dos **carimbos de tempo**, na leitura:

        devido = principal + juros_cristalizados - pago + juros_correntes

    ``juros_correntes`` é ``restante × taxa × dias inteiros`` desde
    ``juros_desde``. Simples e linear, não composto, por um motivo de
    engenharia e não de gosto: linear é aritmética exata em ``Decimal``, e
    composto exigiria potência fracionária — a mesma classe de erro que já
    custou um centavo na curva do crash.

    Dias **inteiros** para que o número não mude enquanto a pessoa olha a
    tela. Dívida criada agora não rende nada hoje.

    Pagamento parcial crava o que já correu (``juros_cristalizados``) e
    reinicia o relógio: assim pagar metade não apaga retroativamente juro que
    já tinha corrido.

    ## O que NÃO existe aqui

    Punição. O dono ainda não decidiu o que se pode ameaçar a quem não paga, e
    inventar isso agora seria decidir por ele. Hoje a dívida é registro, juros
    e pedido. O ponto de extensão é este modelo: quem for implementar sanção
    lê ``devido_em()`` e ``vencida``, sem mexer no resto.
    """

    __tablename__ = "divida"

    id = db.Column(db.Integer, primary_key=True)
    reino_id = db.Column(
        db.Integer, db.ForeignKey("reino.id"), nullable=False, index=True
    )
    devedor_id = db.Column(
        db.Integer, db.ForeignKey("usuario.id"), nullable=False, index=True
    )
    cobranca_id = db.Column(
        db.Integer, db.ForeignKey("cobranca.id"), nullable=True, index=True
    )
    #: Quem cobrou. Uma pessoa, sempre — nunca o cofre.
    cobrada_por_id = db.Column(db.Integer, db.ForeignKey("usuario.id"), nullable=False)

    principal = db.Column(Dinheiro, nullable=False)
    #: Juros já cravados por um pagamento parcial ou pelo congelamento.
    juros_cristalizados = db.Column(Dinheiro, nullable=False, default=ZERO)
    pago = db.Column(Dinheiro, nullable=False, default=ZERO)
    #: De quando os juros correntes contam. Anda a cada pagamento.
    juros_desde = db.Column(db.DateTime(timezone=True), nullable=False, default=agora)

    motivo = db.Column(db.String(200), nullable=False)
    cobrada_em = db.Column(
        db.DateTime(timezone=True), nullable=False, default=agora, index=True
    )
    quitada_em = db.Column(db.DateTime(timezone=True), nullable=True)
    #: A taxa com que ESTA dívida nasceu, em pontos percentuais ao dia.
    #: Congelada: mudar a taxa do reino depois não mexe aqui.
    juros_diarios = db.Column(Dinheiro, nullable=False, default=Decimal("1.00"))
    #: O valor de quitação fixado pelo credor, acumulado (inclui o já pago).
    #: Nulo enquanto ninguém negociou. Preenchido, manda em ``restante`` e
    #: para os juros.
    quitacao = db.Column(Dinheiro, nullable=True)

    reino = db.relationship("Reino", foreign_keys=[reino_id])
    devedor = db.relationship("Usuario", foreign_keys=[devedor_id])

    __table_args__ = (
        CheckConstraint("principal > 0", name="ck_divida_principal"),
        CheckConstraint("pago >= 0", name="ck_divida_pago"),
        CheckConstraint("juros_cristalizados >= 0", name="ck_divida_juros"),
    )

    @property
    def quitada(self):
        return self.quitada_em is not None

    @property
    def negociada(self):
        return self.quitacao is not None

    def __repr__(self):
        return f"<Divida {self.id} principal={self.principal}>"


def buscar_usuario(nome, sessao=None):
    """Acha a conta pelo nome, comparando a forma normalizada.

    É o único jeito de buscar usuário no projeto. Comparar ``nome_usuario``
    direto acha só quem digitou exatamente igual — com o mesmo acento e a
    mesma caixa —, que é justamente o que não se pode exigir de quem está
    digitando no celular.
    """
    sessao = sessao or db.session
    normalizado = normalizar_nome(nome)
    if not normalizado:
        return None
    return sessao.execute(
        db.select(Usuario).where(Usuario.nome_normalizado == normalizado)
    ).scalar_one_or_none()


def banco_central(sessao=None, travado=False):
    """Devolve a conta do Banco Central, ou ``None`` se a gênese não rodou."""
    sessao = sessao or db.session
    consulta = db.select(Usuario).where(
        Usuario.nome_normalizado == USUARIO_BANCO_CENTRAL
    )
    if travado:
        consulta = consulta.with_for_update()
    return sessao.execute(consulta).scalar_one_or_none()
