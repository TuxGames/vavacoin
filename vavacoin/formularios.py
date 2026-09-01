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
