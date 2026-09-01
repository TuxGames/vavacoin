"""Formulários.

Todos herdam de ``FlaskForm``, o que traz o token CSRF de graça — e é por isso
que nenhum formulário do projeto é escrito na mão no template.

O valor da transferência é ``StringField``, não ``DecimalField``: o campo do
WTForms faria a conversão por conta própria, e a regra do projeto é que só
``para_decimal()`` decide o que é dinheiro válido.
"""

from flask_wtf import FlaskForm
from wtforms import PasswordField, StringField, SubmitField
from wtforms.validators import DataRequired, EqualTo, Length, Regexp, ValidationError

from .dinheiro import ZERO, para_decimal

NOME_USUARIO = Regexp(
    r"^[a-z0-9._-]+$",
    message="Use só letras minúsculas, números, ponto, hífen ou sublinhado.",
)


class FormularioLogin(FlaskForm):
    nome_usuario = StringField("Usuário", validators=[DataRequired()])
    senha = PasswordField("Senha", validators=[DataRequired()])
    enviar = SubmitField("Entrar")


class FormularioCadastro(FlaskForm):
    """Cadastro por convite — a única porta de entrada."""

    codigo = StringField("Código do convite", validators=[DataRequired()])
    nome_usuario = StringField(
        "Usuário",
        validators=[DataRequired(), Length(min=3, max=50), NOME_USUARIO],
    )
    nome_exibicao = StringField(
        "Nome que aparece", validators=[DataRequired(), Length(min=2, max=80)]
    )
    senha = PasswordField("Senha", validators=[DataRequired(), Length(min=8, max=200)])
    confirmacao = PasswordField(
        "Repita a senha",
        validators=[DataRequired(), EqualTo("senha", message="As senhas não batem.")],
    )
    enviar = SubmitField("Criar conta e sacar os 50")


class FormularioTransferencia(FlaskForm):
    destinatario = StringField("Para quem", validators=[DataRequired()])
    valor = StringField("Quanto (VVC)", validators=[DataRequired()])
    motivo = StringField("Por quê", validators=[Length(max=200)])
    enviar = SubmitField("Revisar")

    def validate_valor(self, campo):
        """Deixa ``para_decimal()`` mandar: mesma regra do núcleo monetário."""
        try:
            valor = para_decimal(campo.data.strip().replace(",", "."))
        except TypeError as erro:
            raise ValidationError(
                "Valor inválido. Use no máximo dois decimais, como 12.50."
            ) from erro
        if valor <= ZERO:
            raise ValidationError("O valor precisa ser maior que zero.")
        campo.decimal = valor


class FormularioConfirmacao(FlaskForm):
    """Só o token: a transferência confirmada é a que está na sessão.

    O que é confirmado não vem do formulário, e sim do que o servidor guardou
    e mostrou na tela — assim não há como o que foi exibido diferir do que é
    executado.
    """

    token = StringField(validators=[DataRequired()])
    enviar = SubmitField("Confirmar e transferir")


# --- painel do Banco Central ------------------------------------------------


class FormularioEmitirConvite(FlaskForm):
    destinatario = StringField("Para quem (nome do aluno)", validators=[Length(max=80)])
    enviar = SubmitField("Emitir convite")


class FormularioCriarConta(FlaskForm):
    nome_usuario = StringField(
        "Usuário", validators=[DataRequired(), Length(min=3, max=50), NOME_USUARIO]
    )
    nome_exibicao = StringField(
        "Nome que aparece", validators=[DataRequired(), Length(min=2, max=80)]
    )
    senha = PasswordField("Senha", validators=[DataRequired(), Length(min=8, max=200)])
    enviar = SubmitField("Criar conta")


class FormularioAjusteDeSaldo(FlaskForm):
    """Ajuste de saldo: o novo valor e, obrigatoriamente, o porquê.

    O motivo é campo obrigatório aqui e no núcleo. Não é burocracia: um saldo
    que muda sem explicação é indistinguível de um bug, e é o administrador
    quem vai precisar responder pela mudança daqui a seis meses.
    """

    nome_usuario = StringField("Usuário", validators=[DataRequired()])
    novo_saldo = StringField("Saldo correto (VVC)", validators=[DataRequired()])
    motivo = StringField(
        "Motivo", validators=[DataRequired(), Length(min=3, max=300)]
    )
    enviar = SubmitField("Ajustar saldo")

    def validate_novo_saldo(self, campo):
        """Mesma régua do núcleo: quem decide o que é dinheiro é para_decimal."""
        try:
            valor = para_decimal(campo.data.strip().replace(",", "."))
        except TypeError as erro:
            raise ValidationError(
                "Valor inválido. Use no máximo dois decimais, como 12.50."
            ) from erro
        if valor < ZERO:
            raise ValidationError("O saldo não pode ficar negativo.")
        campo.decimal = valor


class FormularioReset(FlaskForm):
    """Reset recolhe o dinheiro de todo mundo; digitar a palavra é o freio."""

    confirmacao = StringField(
        "Digite RESETAR para confirmar", validators=[DataRequired()]
    )
    motivo = StringField("Motivo", validators=[Length(max=300)])
    enviar = SubmitField("Resetar economia")

    def validate_confirmacao(self, campo):
        if campo.data.strip() != "RESETAR":
            raise ValidationError("Digite exatamente RESETAR.")
