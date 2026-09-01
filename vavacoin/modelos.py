"""Modelos do núcleo monetário.

Três tabelas e nada mais nesta fatia: quem tem dinheiro (``Usuario``), o que
autoriza uma pessoa a sacar os 50 (``Convite``) e o registro imutável de todo
centavo que se moveu (``Transacao``).
"""

from datetime import datetime, timezone

import bcrypt
from flask import current_app, has_app_context
from flask_login import UserMixin
from sqlalchemy import CheckConstraint

from .constantes import USUARIO_BANCO_CENTRAL
from .erros import BancoCentralNaoAutentica
from .dinheiro import ZERO, Dinheiro
from .extensoes import db


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
    nome_usuario = db.Column(db.String(50), unique=True, nullable=False, index=True)
    nome_exibicao = db.Column(db.String(80), nullable=False)
    senha_hash = db.Column(db.String(128), nullable=True)
    eh_banco_central = db.Column(db.Boolean, nullable=False, default=False)
    saldo = db.Column(Dinheiro, nullable=False, default=ZERO)
    criado_em = db.Column(db.DateTime(timezone=True), nullable=False, default=agora)

    __table_args__ = (
        # Saldo negativo não é um estado possível desta economia. Se o banco
        # aceitar um, o bug já passou por aqui — a checagem é a última rede.
        CheckConstraint("saldo >= 0", name="ck_usuario_saldo_nao_negativo"),
        # Conta de tesouraria não autentica. No Benbals ela autenticava (com
        # senha em texto puro) e dava para esvaziar o caixa de uma empresa.
        CheckConstraint(
            "NOT eh_banco_central OR senha_hash IS NULL",
            name="ck_banco_central_sem_senha",
        ),
    )

    def definir_senha(self, senha):
        """Guarda o hash bcrypt da senha. Texto puro nunca toca o banco."""
        if self.eh_banco_central:
            raise ValueError("o Banco Central não autentica e não tem senha")
        self.senha_hash = bcrypt.hashpw(
            senha.encode("utf-8"), bcrypt.gensalt(_custo_bcrypt())
        ).decode("utf-8")

    def verificar_senha(self, senha):
        """Confere a senha. Conta sem hash (Banco Central) nunca autentica."""
        if not self.senha_hash:
            return False
        return bcrypt.checkpw(senha.encode("utf-8"), self.senha_hash.encode("utf-8"))

    @property
    def is_active(self):
        """Flask-Login: o Banco Central não é uma sessão que alguém abre."""
        return not self.eh_banco_central

    def get_id(self):
        """Flask-Login: identidade de sessão. O Banco Central não tem uma.

        Estourar aqui é de propósito e é a trava mais interna: ``is_active``
        já barra o ``login_user()`` normal, mas ``login_user(bc, force=True)``
        passaria por cima dela. Nenhuma sessão do BC chega a ser criada.
        """
        if self.eh_banco_central:
            raise BancoCentralNaoAutentica(
                "o Banco Central não abre sessão; seus poderes são por CLI"
            )
        return str(self.id)

    def __repr__(self):
        return f"<Usuario {self.nome_usuario} saldo={self.saldo}>"


class Convite(db.Model):
    """O direito de uma pessoa sacar os 50 iniciais.

    O convite é da **pessoa**, não da conta: é o código que carrega o saque,
    e ele só serve uma vez. Sem isso, dez cadastros da mesma pessoa virariam
    500 VVC e o supply passaria a crescer com o número de contas.
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
    #: NULL só na gênese — o único evento que cria dinheiro, uma vez na vida.
    origem_id = db.Column(db.Integer, db.ForeignKey("usuario.id"), nullable=True, index=True)
    destino_id = db.Column(db.Integer, db.ForeignKey("usuario.id"), nullable=False, index=True)
    valor = db.Column(Dinheiro, nullable=False)
    tipo = db.Column(db.String(30), nullable=False, index=True)
    motivo = db.Column(db.String(200), nullable=True)
    saldo_origem_depois = db.Column(Dinheiro, nullable=True)
    saldo_destino_depois = db.Column(Dinheiro, nullable=False)
    criado_em = db.Column(db.DateTime(timezone=True), nullable=False, default=agora, index=True)

    __table_args__ = (
        CheckConstraint("valor > 0", name="ck_transacao_valor_positivo"),
        CheckConstraint(
            "origem_id IS NULL OR origem_id <> destino_id",
            name="ck_transacao_origem_diferente_destino",
        ),
        # Origem ausente é emissão. Se qualquer tipo além da gênese puder ter
        # origem NULL, o supply deixa de ser fixo — a regra fica no banco.
        CheckConstraint(
            "(origem_id IS NOT NULL) OR tipo = 'genese'",
            name="ck_transacao_sem_origem_so_na_genese",
        ),
    )

    origem = db.relationship("Usuario", foreign_keys=[origem_id])
    destino = db.relationship("Usuario", foreign_keys=[destino_id])

    def __repr__(self):
        return f"<Transacao {self.tipo} {self.valor} -> {self.destino_id}>"


def banco_central(sessao=None, travado=False):
    """Devolve a conta do Banco Central, ou ``None`` se a gênese não rodou."""
    sessao = sessao or db.session
    consulta = db.select(Usuario).where(
        Usuario.nome_usuario == USUARIO_BANCO_CENTRAL
    )
    if travado:
        consulta = consulta.with_for_update()
    return sessao.execute(consulta).scalar_one_or_none()
